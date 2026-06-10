#!/usr/bin/env python3
"""
Kibana Setup: data view → visualizations → dashboard.
Chạy 1 lần bởi kibana-setup container sau khi Kibana khởi động.
"""
import json, urllib.request, urllib.error, time, sys, os

# Fix encoding cho Windows console
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

KIBANA = "http://kibana:5601"
# Cho phép override khi chạy local
if os.environ.get("KIBANA_URL"):
    KIBANA = os.environ["KIBANA_URL"]
DV_ID  = "logs-json-to-excel-dv"
DV_REF = [{"type": "index-pattern", "id": DV_ID, "name": "indexpattern-datasource-layer-l1"}]


# ─────────────────────── helpers ───────────────────────

def wait_for_kibana():
    print("⏳ Waiting for Kibana...", flush=True)
    while True:
        try:
            r = urllib.request.urlopen(f"{KIBANA}/api/status", timeout=5)
            if r.status == 200:
                print("✅ Kibana is ready!\n", flush=True)
                return
        except Exception:
            pass
        time.sleep(5)


def api(method, path, body):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{KIBANA}{path}", data=data, method=method,
        headers={"kbn-xsrf": "true", "Content-Type": "application/json"},
    )
    try:
        r = urllib.request.urlopen(req)
        print(f"  ✓ {path}", flush=True)
        return r.status
    except urllib.error.HTTPError as e:
        msg = e.read().decode()[:200]
        if e.code == 409:
            print(f"  ⊘ {path} (already exists)", flush=True)
        else:
            print(f"  ✗ {path} → {e.code}: {msg}", flush=True)
        return e.code


def saved_object(obj_type, obj_id, attributes, references=None):
    api("POST", f"/api/saved_objects/{obj_type}/{obj_id}?overwrite=true",
        {"attributes": attributes, "references": references or []})


# ─────────────── visualization builders ────────────────

def lens(obj_id, title, vis_type, state):
    saved_object("lens", obj_id, {
        "title": title,
        "visualizationType": vis_type,
        "state": state,   # Kibana expects object, NOT stringified JSON
        "description": "",
    }, DV_REF)


def markdown(obj_id, title, md):
    saved_object("visualization", obj_id, {
        "title": title,
        "visState": json.dumps({
            "type": "markdown",
            "params": {"markdown": md, "fontSize": 12, "openLinksInNewTab": True},
            "aggs": [],
        }),
        "uiStateJSON": "{}",
        "description": "",
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []}
            )
        },
    })


def metric_state(op, field, label, query="", color=None):
    viz = {"layerId": "l1", "layerType": "data", "metricAccessor": "m"}
    if color:
        viz["color"] = color
    return {
        "datasourceStates": {"formBased": {"layers": {"l1": {
            "columns": {"m": {
                "operationType": op, "dataType": "number",
                "isBucketed": False, "sourceField": field, "label": label,
            }},
            "columnOrder": ["m"], "incompleteColumns": {},
        }}}},
        "visualization": viz,
        "query": {"query": query, "language": "kuery"},
        "filters": [],
    }


# ──────────────────────── main ─────────────────────────

def main():
    wait_for_kibana()

    # ── 1. Log sources (APM trace-log correlation) ──
    print("=== 1. Log sources ===", flush=True)
    saved_object("infrastructure-ui-source", "default", {
        "name": "Default", "description": "",
        "logIndices": {"type": "index_name",
                       "indexName": "logs-*-*,filebeat-*,logs-json_to_excel-default"},
        "metricAlias": "metrics-*,metricbeat-*",
    })

    # ── 2. Data view ──
    print("=== 2. Data view ===", flush=True)
    # Xóa data view cũ (nếu có) với title trùng nhưng ID khác
    try:
        r = urllib.request.urlopen(f"{KIBANA}/api/data_views", timeout=10)
        views = json.loads(r.read()).get("data_view", [])
        for v in views:
            if v["title"] in ("logs-json_to_excel-*,filebeat-*", "logs-json_to_excel-*") and v["id"] != DV_ID:
                print(f"  Deleting old data view {v['id']}...", flush=True)
                req = urllib.request.Request(
                    f"{KIBANA}/api/data_views/data_view/{v['id']}",
                    method="DELETE",
                    headers={"kbn-xsrf": "true"},
                )
                urllib.request.urlopen(req)
    except Exception as e:
        print(f"  (cleanup skipped: {e})", flush=True)

    status = api("POST", "/api/data_views/data_view", {"data_view": {
        "id": DV_ID,
        "title": "logs-json_to_excel-*,filebeat-*",
        "name": "App Logs (json-to-excel)",
        "timeFieldName": "@timestamp",
    }})

    # ── 3. Visualizations ──
    print("=== 3. Visualizations ===", flush=True)


    lens("vis-total",  "📝 Total Logs",    "lnsMetric",
         metric_state("count", "___records___", "Total"))

    lens("vis-errors", "🔴 Error Logs",    "lnsMetric",
         metric_state("count", "___records___", "Errors", "log.level : ERROR", "#BD271E"))

    lens("vis-traces", "🔗 Unique Traces", "lnsMetric",
         metric_state("unique_count", "trace.id", "Traces"))

    lens("vis-timeline", "📊 Logs Over Time by Level", "lnsXY", {
        "datasourceStates": {"formBased": {"layers": {"l1": {
            "columns": {
                "d":  {"operationType": "date_histogram", "dataType": "date",   "isBucketed": True,
                        "sourceField": "@timestamp", "params": {"interval": "auto"}, "scale": "interval"},
                "lv": {"operationType": "terms",          "dataType": "string", "isBucketed": True,
                        "sourceField": "log.level", "scale": "ordinal",
                        "params": {"size": 5, "orderBy": {"type": "column", "columnId": "c"}, "orderDirection": "desc"}},
                "c":  {"operationType": "count",          "dataType": "number", "isBucketed": False,
                        "sourceField": "___records___", "scale": "ratio"},
            },
            "columnOrder": ["lv", "d", "c"], "incompleteColumns": {},
        }}}},
        "visualization": {
            "preferredSeriesType": "bar_stacked",
            "layers": [{"layerId": "l1", "layerType": "data", "seriesType": "bar_stacked",
                        "accessors": ["c"], "xAccessor": "d", "splitAccessor": "lv"}],
            "legend": {"isVisible": True, "position": "right"},
        },
        "query": {"query": "", "language": "kuery"}, "filters": [],
    })

    lens("vis-donut", "🎯 Level Distribution", "lnsPie", {
        "datasourceStates": {"formBased": {"layers": {"l1": {
            "columns": {
                "lv": {"operationType": "terms", "dataType": "string", "isBucketed": True,
                        "sourceField": "log.level", "scale": "ordinal",
                        "params": {"size": 10, "orderBy": {"type": "column", "columnId": "c"}, "orderDirection": "desc"}},
                "c":  {"operationType": "count", "dataType": "number", "isBucketed": False,
                        "sourceField": "___records___", "scale": "ratio"},
            },
            "columnOrder": ["lv", "c"], "incompleteColumns": {},
        }}}},
        "visualization": {
            "shape": "donut",
            "layers": [{"layerId": "l1", "layerType": "data",
                        "primaryGroups": ["lv"], "metrics": ["c"],
                        "numberDisplay": "percent", "categoryDisplay": "default", "legendDisplay": "default"}],
        },
        "query": {"query": "", "language": "kuery"}, "filters": [],
    })

    lens("vis-error-loggers", "🔴 Top Error Loggers", "lnsDatatable", {
        "datasourceStates": {"formBased": {"layers": {"l1": {
            "columns": {
                "lg": {"operationType": "terms", "dataType": "string", "isBucketed": True,
                        "sourceField": "log.logger", "scale": "ordinal",
                        "params": {"size": 15, "orderBy": {"type": "column", "columnId": "c"}, "orderDirection": "desc"}},
                "c":  {"operationType": "count", "dataType": "number", "isBucketed": False,
                        "sourceField": "___records___", "scale": "ratio", "label": "Count"},
            },
            "columnOrder": ["lg", "c"], "incompleteColumns": {},
        }}}},
        "visualization": {"layerId": "l1", "layerType": "data",
                          "columns": [{"columnId": "lg", "width": 400}, {"columnId": "c"}]},
        "query": {"query": "log.level : ERROR", "language": "kuery"}, "filters": [],
    })

    lens("vis-trace-volume", "🔗 Top Traces by Log Volume", "lnsDatatable", {
        "datasourceStates": {"formBased": {"layers": {"l1": {
            "columns": {
                "tr": {"operationType": "terms", "dataType": "string", "isBucketed": True,
                        "sourceField": "trace.id", "scale": "ordinal",
                        "params": {"size": 15, "orderBy": {"type": "column", "columnId": "c"}, "orderDirection": "desc"}},
                "c":  {"operationType": "count", "dataType": "number", "isBucketed": False,
                        "sourceField": "___records___", "scale": "ratio", "label": "Logs"},
            },
            "columnOrder": ["tr", "c"], "incompleteColumns": {},
        }}}},
        "visualization": {"layerId": "l1", "layerType": "data",
                          "columns": [{"columnId": "tr", "width": 400}, {"columnId": "c"}]},
        "query": {"query": "", "language": "kuery"}, "filters": [],
    })

    # ── 4. Dashboard (references saved visualizations) ──
    print("=== 4. Dashboard ===", flush=True)

    panels = [
        {"version": "8.17.0", "type": "lens", "panelIndex": "2", "gridData": {"x": 0,  "y": 0,  "w": 16, "h": 8,  "i": "2"}, "panelRefName": "panel_1"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "3", "gridData": {"x": 16, "y": 0,  "w": 16, "h": 8,  "i": "3"}, "panelRefName": "panel_2"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "4", "gridData": {"x": 32, "y": 0,  "w": 16, "h": 8,  "i": "4"}, "panelRefName": "panel_3"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "5", "gridData": {"x": 0,  "y": 8,  "w": 32, "h": 12, "i": "5"}, "panelRefName": "panel_4"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "6", "gridData": {"x": 32, "y": 8,  "w": 16, "h": 12, "i": "6"}, "panelRefName": "panel_5"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "7", "gridData": {"x": 0,  "y": 20, "w": 24, "h": 12, "i": "7"}, "panelRefName": "panel_6"},
        {"version": "8.17.0", "type": "lens", "panelIndex": "8", "gridData": {"x": 24, "y": 20, "w": 24, "h": 12, "i": "8"}, "panelRefName": "panel_7"},
    ]
    refs = [
        {"type": "lens",          "id": "vis-total",         "name": "panel_1"},
        {"type": "lens",          "id": "vis-errors",        "name": "panel_2"},
        {"type": "lens",          "id": "vis-traces",        "name": "panel_3"},
        {"type": "lens",          "id": "vis-timeline",      "name": "panel_4"},
        {"type": "lens",          "id": "vis-donut",         "name": "panel_5"},
        {"type": "lens",          "id": "vis-error-loggers", "name": "panel_6"},
        {"type": "lens",          "id": "vis-trace-volume",  "name": "panel_7"},
    ]

    saved_object("dashboard", "app-overview", {
        "title": "📊 json-to-excel — Log Analytics",
        "description": "Log analytics dashboard. Traces & Metrics → Kibana APM.",
        "panelsJSON": json.dumps(panels),
        "timeRestore": True,
        "timeTo": "now",
        "timeFrom": "now-24h",
        "refreshInterval": {"pause": False, "value": 30000},
        "kibanaSavedObjectMeta": {
            "searchSourceJSON": json.dumps(
                {"query": {"query": "", "language": "kuery"}, "filter": []}
            )
        },
    }, refs)

    print("\n" + "=" * 50, flush=True)
    print("✅ Kibana setup complete!", flush=True)
    print("📊 Dashboard: http://localhost:5601/app/dashboards#/view/app-overview", flush=True)
    print("=" * 50, flush=True)


if __name__ == "__main__":
    main()

