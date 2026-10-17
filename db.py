"""Load the CSVs in data/ into a local SQLite db (data/oilops.db).

Swap-in path for the real organizer data: replace the CSVs in data/
(keep the same column names, or adjust the queries in agent/tools.py)
and run this again.
"""
import os
import sqlite3
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
DB = os.path.join(DATA, "oilops.db")

TABLES = {
    "wells": "wells.csv",
    "sensor_readings": "sensor_readings.csv",
    "maintenance": "maintenance.csv",
}


def build():
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    for table, fn in TABLES.items():
        df = pd.read_csv(os.path.join(DATA, fn))
        df.to_sql(table, con, index=False)
    con.commit()
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in TABLES}
    con.close()
    return n


if __name__ == "__main__":
    print("built oilops.db:", build())
