"""Tools the agent can call. All read-only over the SQLite db.

Each tool returns a small JSON-able dict. Tools are also what the demo
(offline) mode uses, so they double as the deterministic query layer.
"""
import os
import re
import sqlite3

DB = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "oilops.db"))

METRICS = {
    "pressure": "pressure_psi",
    "temperature": "temperature_f",
    "flow": "flow_rate_bpd",
    "flow_rate": "flow_rate_bpd",
    "vibration": "vibration_mm_s",
    "water_cut": "water_cut_pct",
}


def _con():
    # read-only so even raw SQL can't write
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def _rows(con, sql, args=()):
    cur = con.execute(sql, args)
    cols = [c[0] for c in cur.description]
    return cols, cur.fetchall()


def list_wells(block=None, status=None):
    """Wells with their static info; filter by block and/or status."""
    sql = "select well_id, block, well_type, status, issue, install_date from wells"
    conds, args = [], []
    if block:
        conds.append("lower(block) = lower(?)")
        args.append(block)
    if status:
        conds.append("lower(status) = lower(?)")
        args.append(status.replace("_", "-") if "_" not in status else status)
    if conds:
        sql += " where " + " and ".join(conds)
    sql += " order by block, well_id"
    con = _con()
    cols, rows = _rows(con, sql, args)
    con.close()
    return {"columns": cols, "rows": [list(r) for r in rows]}


def sensor_summary(well_id=None, block=None, days=30):
    """Avg/min/max per metric over the last N days, per well."""
    d = int(days)
    con = _con()
    cutoff = con.execute(
        "select date(max(date), ?) from sensor_readings", (f"-{d - 1} days",)
    ).fetchone()[0]
    sql = (
        "select s.well_id, w.block, w.status, "
        "round(avg(s.pressure_psi),1) avg_pressure, "
        "round(avg(s.flow_rate_bpd),1) avg_flow, "
        "round(avg(s.vibration_mm_s),2) avg_vibration, "
        "round(avg(s.water_cut_pct),1) avg_water_cut, "
        "round(max(s.vibration_mm_s),2) max_vibration "
        "from sensor_readings s join wells w on w.well_id = s.well_id "
        "where s.date >= ?"
    )
    args = [cutoff]
    if well_id:
        sql += " and lower(s.well_id) = lower(?)"
        args.append(well_id)
    if block:
        sql += " and lower(w.block) = lower(?)"
        args.append(block)
    sql += " group by s.well_id order by s.well_id"
    cols, rows = _rows(con, sql, args)
    con.close()
    return {"columns": cols, "rows": [list(r) for r in rows], "window_days": d}


def sensor_series(well_id, metric="flow", days=60):
    """Daily time series of one metric for one well (for line charts)."""
    col = METRICS.get(metric.lower().replace(" ", "_"))
    if not col:
        return {"error": f"unknown metric '{metric}', options: {sorted(METRICS)}"}
    con = _con()
    cols, rows = _rows(
        con,
        f"select date, {col} from sensor_readings where lower(well_id)=lower(?) "
        "order by date desc limit ?",
        (well_id, int(days)),
    )
    con.close()
    rows = rows[::-1]
    return {"well_id": well_id, "metric": col,
            "x": [r[0] for r in rows], "y": [r[1] for r in rows]}


def maintenance_status(overdue_only=False):
    """Maintenance records joined with well status; overdue flags vs latest data date."""
    con = _con()
    today = con.execute("select max(date) from sensor_readings").fetchone()[0]
    sql = (
        "select m.well_id, w.block, w.status, m.last_service, m.service_type, "
        "m.next_service_due, m.technician, "
        "case when m.next_service_due < ? then 'OVERDUE' else 'ok' end as overdue_flag "
        "from maintenance m join wells w on w.well_id = m.well_id"
    )
    if overdue_only:
        sql += " where m.next_service_due < ?"
    sql += " order by m.next_service_due"
    args = (today, today) if overdue_only else (today,)
    cols, rows = _rows(con, sql, args)
    con.close()
    return {"columns": cols, "rows": [list(r) for r in rows], "as_of": today}


def non_producing_by_block():
    """Non-producing wells grouped by block with their issue."""
    con = _con()
    cols, rows = _rows(
        con,
        "select block, issue, count(*) as wells, "
        "group_concat(well_id, ', ') as well_ids "
        "from wells where status='non-producing' group by block, issue "
        "order by block, issue",
    )
    # also per-block totals for a chart
    _, totals = _rows(
        con,
        "select block, count(*) from wells where status='non-producing' "
        "group by block order by block",
    )
    con.close()
    return {"columns": cols, "rows": [list(r) for r in rows],
            "chart": {"x": [t[0] for t in totals], "y": [t[1] for t in totals]}}


def correlate(well_id, metric_a="vibration", metric_b="flow", days=60):
    """Pearson correlation between two metrics for one well + both series."""
    ca, cb = METRICS.get(metric_a.lower()), METRICS.get(metric_b.lower())
    if not ca or not cb:
        return {"error": f"unknown metric, options: {sorted(METRICS)}"}
    con = _con()
    _, rows = _rows(
        con,
        f"select date, {ca}, {cb} from sensor_readings "
        "where lower(well_id)=lower(?) order by date desc limit ?",
        (well_id, int(days)),
    )
    con.close()
    rows = rows[::-1]
    xs = [r[1] for r in rows]
    ys = [r[2] for r in rows]
    n = len(xs)
    if n < 3:
        return {"error": "not enough data points"}
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    sx = sum((a - mx) ** 2 for a in xs) ** 0.5
    sy = sum((b - my) ** 2 for b in ys) ** 0.5
    r = round(cov / (sx * sy), 3) if sx and sy else None
    return {"well_id": well_id, "metric_a": ca, "metric_b": cb,
            "pearson_r": r, "n_points": n,
            "x": [r0[0] for r0 in rows], "y_a": xs, "y_b": ys}


def correlation_heatmap(days=30):
    """Correlation matrix across all metrics (fleet-wide, last N days)."""
    con = _con()
    cutoff = con.execute(
        "select date(max(date), ?) from sensor_readings", (f"-{int(days) - 1} days",)
    ).fetchone()[0]
    cols, rows = _rows(
        con,
        "select pressure_psi, temperature_f, flow_rate_bpd, vibration_mm_s, "
        "water_cut_pct from sensor_readings where date >= ?",
        (cutoff,),
    )
    con.close()
    n = len(rows)
    means = [sum(r[i] for r in rows) / n for i in range(len(cols))]
    def corr(i, j):
        cov = sum((r[i] - means[i]) * (r[j] - means[j]) for r in rows)
        si = sum((r[i] - means[i]) ** 2 for r in rows) ** 0.5
        sj = sum((r[j] - means[j]) ** 2 for r in rows) ** 0.5
        return round(cov / (si * sj), 2) if si and sj else 0.0
    z = [[corr(i, j) for j in range(len(cols))] for i in range(len(cols))]
    return {"labels": cols, "z": z, "window_days": int(days)}


def run_sql(query):
    """Read-only escape hatch for questions the other tools don't cover."""
    q = query.strip().rstrip(";")
    if not re.match(r"^(select|with)\b", q, re.I) or ";" in q:
        return {"error": "only a single SELECT/WITH statement is allowed"}
    try:
        con = _con()
        cols, rows = _rows(con, q + " limit 200")
        con.close()
        return {"columns": cols, "rows": [list(r) for r in rows]}
    except sqlite3.Error as e:
        return {"error": str(e)}


# schemas advertised to the LLM (OpenAI/Groq tool-calling format)
def _tool(name, description, properties=None, required=None):
    params = {"type": "object", "properties": properties or {}}
    if required:
        params["required"] = required
    return {"type": "function", "function": {
        "name": name, "description": description, "parameters": params}}


_str = {"type": "string"}
_int = {"type": "integer"}
_bool = {"type": "boolean"}

TOOL_SCHEMAS = [
    _tool("list_wells",
          "List wells with block, type, status, issue and install date. Optional filters.",
          {"block": _str,
           "status": dict(_str, description="producing or non-producing")}),
    _tool("sensor_summary",
          "Average pressure/flow/vibration/water-cut per well over the last N days.",
          {"well_id": _str, "block": _str, "days": dict(_int, default=30)}),
    _tool("sensor_series",
          "Daily time series of one metric for one well (for line charts).",
          {"well_id": _str, "metric": _str, "days": dict(_int, default=60)},
          required=["well_id"]),
    _tool("maintenance_status",
          "Maintenance records with overdue flags. Use overdue_only=true for just overdue wells.",
          {"overdue_only": dict(_bool, default=False)}),
    _tool("non_producing_by_block",
          "Non-producing wells grouped by block with reasons (issues), plus per-block counts for a chart."),
    _tool("correlate",
          "Pearson correlation between two sensor metrics for one well, with both series.",
          {"well_id": _str, "metric_a": _str, "metric_b": _str,
           "days": dict(_int, default=60)},
          required=["well_id"]),
    _tool("correlation_heatmap",
          "Correlation matrix across all sensor metrics, fleet-wide, last N days (for heat maps).",
          {"days": dict(_int, default=30)}),
    _tool("run_sql",
          "Run a read-only SELECT over tables wells, sensor_readings, maintenance. Use for questions other tools can't answer.",
          {"query": _str}, required=["query"]),
]

TOOL_FUNCS = {
    "list_wells": list_wells,
    "sensor_summary": sensor_summary,
    "sensor_series": sensor_series,
    "maintenance_status": maintenance_status,
    "non_producing_by_block": non_producing_by_block,
    "correlate": correlate,
    "correlation_heatmap": correlation_heatmap,
    "run_sql": run_sql,
}
