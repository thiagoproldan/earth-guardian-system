"""Curated tier - the relational store the thesis specifies alongside the NoSQL one.

Where the document store keeps uplinks as they arrived, this keeps them as
questions get asked of them: typed columns, one row per reading, indexed by plot
and time, plus the daily aggregates and the advisories GAIA produced.

SQLite is a deliberate choice rather than a compromise. It is one file, it ships
with Python, it handles the row counts a three-plot fleet produces without
noticing, and a reviewer can open it with anything. WAL mode keeps the console
readable while a simulation is still writing.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    plot_id            TEXT NOT NULL,
    timestamp          TEXT NOT NULL,
    dev_eui            TEXT,
    frame_counter      INTEGER,
    soil_moisture      REAL,
    air_temp_c         REAL,
    humidity_pct       REAL,
    lux                REAL,
    soil_ph            REAL,
    air_quality_ppm    REAL,
    battery_pct        REAL,
    data_rate          INTEGER,
    spreading_factor   INTEGER,
    rssi_dbm           REAL,
    snr_db             REAL,
    PRIMARY KEY (plot_id, timestamp)
);

CREATE TABLE IF NOT EXISTS ground_truth (
    plot_id          TEXT NOT NULL,
    date             TEXT NOT NULL,
    depletion_mm     REAL,
    theta_true       REAL,
    taw_mm           REAL,
    raw_mm           REAL,
    root_depth_m     REAL,
    kc               REAL,
    et0_mm           REAL,
    etc_potential_mm REAL,
    etc_actual_mm    REAL,
    ks               REAL,
    rain_mm          REAL,
    irrigation_mm    REAL,
    drainage_mm      REAL,
    runoff_mm        REAL,
    cycle            INTEGER,
    PRIMARY KEY (plot_id, date)
);

CREATE TABLE IF NOT EXISTS site_events (
    plot_id   TEXT NOT NULL,
    date      TEXT NOT NULL,
    kind      TEXT NOT NULL,
    magnitude REAL
);

CREATE INDEX IF NOT EXISTS idx_readings_plot_ts ON readings (plot_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_plot      ON site_events (plot_id, date);
"""

#: Tables GAIA rewrites wholesale on every analysis run, so their schema follows
#: whatever the analysis produced rather than being pinned here.
DERIVED_TABLES = {"daily_kpi", "advisories", "irrigation_plans"}


class CuratedStore:
    """Typed, queryable view of the raw zone."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30.0)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            yield connection
            connection.commit()
        finally:
            connection.close()

    def write_frame(self, table: str, frame: pd.DataFrame, replace: bool = True) -> int:
        if frame.empty:
            return 0
        payload = frame.copy()
        if "timestamp" in payload:
            payload["timestamp"] = pd.to_datetime(payload["timestamp"]).dt.strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        for column in ("date", "due_on", "raised_on"):
            if column in payload:
                payload[column] = pd.to_datetime(payload[column]).dt.strftime("%Y-%m-%d")
        for column in payload.columns:
            if payload[column].dtype == bool:
                payload[column] = payload[column].astype(int)

        with self.connect() as connection:
            if table in DERIVED_TABLES:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            elif replace and "plot_id" in payload:
                plots = sorted(payload["plot_id"].unique())
                placeholders = ",".join("?" * len(plots))
                connection.execute(f"DELETE FROM {table} WHERE plot_id IN ({placeholders})", plots)
            payload.to_sql(table, connection, if_exists="append", index=False)
        return len(payload)

    def read_frame(
        self,
        table: str,
        plot_id: str | None = None,
        start: str | None = None,
        end: str | None = None,
        columns: str = "*",
    ) -> pd.DataFrame:
        clauses, params = [], []
        if plot_id:
            clauses.append("plot_id = ?")
            params.append(plot_id)
        time_column = "timestamp" if table == "readings" else "date"
        if start:
            clauses.append(f"{time_column} >= ?")
            params.append(start)
        if end:
            clauses.append(f"{time_column} <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {columns} FROM {table}{where} ORDER BY plot_id, {time_column}"

        with self.connect() as connection:
            frame = pd.read_sql_query(sql, connection, params=params)
        for column in ("timestamp", "date"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column])
        return frame

    def query(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with self.connect() as connection:
            return pd.read_sql_query(sql, connection, params=params)

    def table_stats(self) -> pd.DataFrame:
        rows = []
        with self.connect() as connection:
            tables = [
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                (count,) = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                rows.append({"table": table, "rows": count})
        return pd.DataFrame(rows)

    @property
    def size_mb(self) -> float:
        return self.path.stat().st_size / 1_048_576 if self.path.exists() else 0.0
