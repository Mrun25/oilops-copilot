"""Offline fallback: pattern-matched answers with zero API calls.

Keeps the whole UI demo-able at the venue even before an API key is
set. Covers the questions the briefing called out; anything else gets
a pointer at what to ask. The LLM path (agent/core.py) takes over as
soon as LLM_API_KEY is set.
"""
import re

from . import tools


def _well_id(text):
    m = re.search(r"\b(w-[a-c]-\d{3})\b", text.lower())
    return m.group(1).upper() if m else None


def answer(question):
    q = question.lower()

    if "non-produc" in q or "not producing" in q or "down well" in q:
        data = tools.non_producing_by_block()
        blocks = data["chart"]
        n = sum(blocks["y"])
        return {
            "message": f"{n} wells are non-producing across {len(blocks['x'])} blocks. "
                       "Breakdown with reasons below.",
            "components": [
                {"component": "bar_chart", "title": "Non-producing wells by block",
                 "x": blocks["x"], "y": blocks["y"],
                 "x_label": "Block", "y_label": "Wells"},
                {"component": "table", "title": "By block and reason",
                 "columns": data["columns"], "rows": data["rows"]},
            ],
        }

    if "maintenance" in q or "overdue" in q or "service" in q:
        overdue = "overdue" in q
        data = tools.maintenance_status(overdue_only=overdue)
        n = len(data["rows"])
        return {
            "message": (f"{n} maintenance records are overdue as of {data['as_of']}."
                        if overdue else
                        f"{n} maintenance records on file (as of {data['as_of']}); "
                        "the OVERDUE flag marks anything past its due date."),
            "components": [{"component": "table",
                            "title": "Overdue maintenance" if overdue else "Maintenance",
                            "columns": data["columns"], "rows": data["rows"]}],
        }

    if "correlat" in q or "heatmap" in q or "heat map" in q:
        well = _well_id(q)
        if well:
            data = tools.correlate(well, "vibration", "flow")
            if "error" in data:
                return {"message": data["error"], "components": []}
            return {
                "message": f"For {well}, vibration vs flow has a Pearson r of "
                           f"{data['pearson_r']} over {data['n_points']} days.",
                "components": [
                    {"component": "metric", "label": f"{well} vibration/flow correlation",
                     "value": str(data["pearson_r"])},
                    {"component": "line_chart", "title": f"{well}: vibration vs flow",
                     "x": data["x"],
                     "series": [{"name": "vibration_mm_s", "y": data["y_a"]},
                                {"name": "flow_rate_bpd", "y": data["y_b"]}]},
                ],
            }
        data = tools.correlation_heatmap()
        return {
            "message": "Fleet-wide correlation between all sensor metrics "
                       f"over the last {data['window_days']} days.",
            "components": [{"component": "heatmap", "title": "Sensor metric correlations",
                            "x_labels": data["labels"], "y_labels": data["labels"],
                            "z": data["z"]}],
        }

    if any(w in q for w in ("trend", "series", "over time", "plot", "chart", "history")) or _well_id(q):
        well = _well_id(q)
        if not well:
            return {"message": "Which well? e.g. 'flow trend for W-A-101'.",
                    "components": []}
        metric = "flow"
        for m in ("pressure", "vibration", "temperature", "water_cut"):
            if m in q:
                metric = m
                break
        data = tools.sensor_series(well, metric)
        if "error" in data:
            return {"message": data["error"], "components": []}
        return {
            "message": f"Daily {data['metric']} for {well} over the recorded period.",
            "components": [{"component": "line_chart",
                            "title": f"{well}: {data['metric']}",
                            "x": data["x"],
                            "series": [{"name": data["metric"], "y": data["y"]}]}],
        }

    if "well" in q or "block" in q or "status" in q:
        block = None
        for b in ("north", "east", "south"):
            if b in q:
                block = b.capitalize()
        status = "producing" if "producing" in q and "non" not in q else None
        data = tools.list_wells(block=block, status=status)
        return {
            "message": f"{len(data['rows'])} wells match.",
            "components": [{"component": "table", "title": "Wells",
                            "columns": data["columns"], "rows": data["rows"]}],
        }

    return {
        "message": "Offline demo mode (no LLM_API_KEY set). I can answer: "
                   "non-producing wells by block, maintenance/overdue, "
                   "correlations or a heatmap, trends per well (e.g. "
                   "'flow trend for W-A-101'), and well lists.",
        "components": [],
    }
