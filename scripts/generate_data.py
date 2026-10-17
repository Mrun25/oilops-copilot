"""Generate the synthetic wells / sensors / maintenance dataset.

Stand-in for the organizer sample data. Column names follow what was
described at the briefing (well identity, install date, type, last
service, daily sensor readings), so the real CSVs should be a drop-in
replace in data/ + rerun db.py.
"""
import csv
import math
import os
import random
import datetime

random.seed(42)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.abspath(os.path.join(HERE, "..", "data"))
os.makedirs(DATA, exist_ok=True)

DAYS = 60
END = datetime.date(2026, 10, 15)
START = END - datetime.timedelta(days=DAYS - 1)

# name, block, well_type, status, issue (None if fine), install date
WELLS = [
    ("W-A-101", "North", "oil",     "producing",     None,                     "2019-03-14"),
    ("W-A-102", "North", "oil",     "producing",     None,                     "2020-07-02"),
    ("W-A-103", "North", "gas",     "producing",     None,                     "2018-11-30"),
    ("W-A-104", "North", "oil",     "non-producing", "pump_failure",           "2017-05-21"),
    ("W-A-105", "North", "injector","producing",     None,                     "2021-01-18"),
    ("W-A-106", "North", "oil",     "non-producing", "high_water_cut",         "2016-09-09"),
    ("W-B-201", "East",  "oil",     "producing",     None,                     "2019-12-05"),
    ("W-B-202", "East",  "gas",     "producing",     None,                     "2020-04-27"),
    ("W-B-203", "East",  "oil",     "non-producing", "low_reservoir_pressure", "2015-08-16"),
    ("W-B-204", "East",  "oil",     "producing",     None,                     "2022-02-11"),
    ("W-B-205", "East",  "injector","producing",     None,                     "2021-06-23"),
    ("W-B-206", "East",  "oil",     "non-producing", "shut_in_maintenance",    "2018-03-08"),
    ("W-C-301", "South", "oil",     "producing",     None,                     "2020-10-19"),
    ("W-C-302", "South", "gas",     "producing",     None,                     "2019-01-25"),
    ("W-C-303", "South", "oil",     "producing",     None,                     "2021-09-14"),
    ("W-C-304", "South", "oil",     "non-producing", "pump_failure",           "2017-12-01"),
    ("W-C-305", "South", "gas",     "producing",     None,                     "2023-04-07"),
    ("W-C-306", "South", "oil",     "producing",     None,                     "2022-08-29"),
]

SERVICE_TYPES = ["pump_overhaul", "valve_replacement", "sensor_calibration",
                 "well_cleanout", "pressure_test", "corrosion_inspection"]
TECHS = ["R. Iyer", "S. Kulkarni", "A. Mehta", "P. Nair", "V. Rao"]


def daterange():
    return [START + datetime.timedelta(days=i) for i in range(DAYS)]


def gen_well_series(status, issue, idx):
    """Daily (pressure, temp, flow, vibration, water_cut) for one well."""
    base_p = random.uniform(1800, 3200)
    base_t = random.uniform(140, 210)
    base_f = random.uniform(250, 900)
    base_v = random.uniform(1.5, 3.5)
    base_w = random.uniform(5, 25)
    rows = []
    for i, _ in enumerate(daterange()):
        drift = i / DAYS
        noise = lambda s=1.0: random.gauss(0, s)
        if status == "producing":
            p = base_p - 3 * i + noise(15)
            t = base_t + noise(1.5)
            f = max(0, base_f - 1.2 * i + noise(20))
            v = base_v + noise(0.2)
            w = base_w + 4 * drift + noise(0.8)
        else:
            # dead or dying well: no flow, pressure bleeding off, quirks per issue
            f = max(0, noise(3))
            p = base_p * (0.55 - 0.2 * drift) + noise(10)
            t = base_t - 20 * drift + noise(1.5)
            if issue == "pump_failure":
                v = 7.5 + 1.5 * math.sin(i / 4.0) + noise(0.4)  # vibration signature
            elif issue == "low_reservoir_pressure":
                v = base_v + noise(0.2)
                p = base_p * (0.3 - 0.1 * drift) + noise(8)
            elif issue == "high_water_cut":
                v = base_v + noise(0.2)
            else:  # shut_in_maintenance
                v = 0.5 + noise(0.1)
            w = 85 + noise(3) if issue == "high_water_cut" else base_w + noise(2)
        # one producing well with a rising vibration trend (good correlation demo)
        if idx == 0:
            v = base_v + 2.5 * drift + noise(0.2)
        rows.append((round(p, 1), round(t, 1), round(f, 1), round(v, 2), round(w, 1)))
    return rows


def main():
    # wells.csv
    with open(os.path.join(DATA, "wells.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["well_id", "block", "well_type", "status", "issue",
                    "install_date"])
        for name, block, wtype, status, issue, inst in WELLS:
            w.writerow([name, block, wtype, status, issue or "", inst])

    # sensor_readings.csv
    with open(os.path.join(DATA, "sensor_readings.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["well_id", "date", "pressure_psi", "temperature_f",
                    "flow_rate_bpd", "vibration_mm_s", "water_cut_pct"])
        for idx, (name, _, _, status, issue, _) in enumerate(WELLS):
            series = gen_well_series(status, issue, idx)
            for day, vals in zip(daterange(), series):
                w.writerow([name, day.isoformat(), *vals])

    # maintenance.csv
    with open(os.path.join(DATA, "maintenance.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["well_id", "last_service", "service_type",
                    "next_service_due", "technician", "notes"])
        for name, *_rest in WELLS:
            last = END - datetime.timedelta(days=random.randint(10, 400))
            due = last + datetime.timedelta(days=180)
            w.writerow([name, last.isoformat(), random.choice(SERVICE_TYPES),
                        due.isoformat(), random.choice(TECHS),
                        "routine" if due > END else "overdue, schedule ASAP"])

    counts = {}
    for fn in ("wells.csv", "sensor_readings.csv", "maintenance.csv"):
        with open(os.path.join(DATA, fn)) as fh:
            counts[fn] = sum(1 for _ in fh) - 1
    print("wrote:", counts)


if __name__ == "__main__":
    main()
