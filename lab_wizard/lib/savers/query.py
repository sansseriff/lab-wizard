from __future__ import annotations

"""
Read-side helpers for analysis notebooks. Wraps SQL queries behind named
functions so callers don't write SQL by hand.

All helpers accept a ``db_path`` to a SQLite file produced by DatabaseSaver
and return pandas DataFrames.
"""

import json
from typing import Any

import pandas as pd
from sqlalchemy import create_engine


def _engine(db_path: str) -> Any:
    return create_engine(f"sqlite:///{db_path}")


def get_measurements(db_path: str, run_id: int) -> pd.DataFrame:
    """All measurements in a run, with metadata expanded into columns."""
    engine = _engine(db_path)
    df = pd.read_sql(
        """
        SELECT id, timestamp, counts, int_time, delta_time, temperature, metadata
        FROM measurements
        WHERE run_id = ?
        ORDER BY timestamp
        """,
        engine,
        params=(run_id,),
    )
    if df.empty:
        return df
    metadata_df = pd.json_normalize(
        df["metadata"].apply(lambda v: json.loads(v) if isinstance(v, str) else (v or {}))
    )
    return pd.concat([df.drop("metadata", axis=1), metadata_df], axis=1)


def get_runs(
    db_path: str,
    cryostat_name: str | None = None,
    run_type: str | None = None,
    device_name: str | None = None,
    since: str | None = None,
) -> pd.DataFrame:
    """Filter runs by various criteria, joining in cryostat/device names."""
    engine = _engine(db_path)
    query = """
        SELECT r.id, r.run_type, r.started_at, r.ended_at, r.operator,
               r.description, c.name as cryostat,
               d.name as device, w.name as wafer
        FROM runs r
        JOIN cryostats c ON r.cryostat_id = c.id
        LEFT JOIN devices d ON r.device_id = d.id
        LEFT JOIN wafers w ON d.wafer_id = w.id
        WHERE 1=1
    """
    params: list[Any] = []
    if cryostat_name:
        query += " AND c.name = ?"
        params.append(cryostat_name)
    if run_type:
        query += " AND r.run_type = ?"
        params.append(run_type)
    if device_name:
        query += " AND d.name = ?"
        params.append(device_name)
    if since:
        query += " AND r.started_at >= ?"
        params.append(since)
    query += " ORDER BY r.started_at DESC"
    return pd.read_sql(query, engine, params=tuple(params))


def get_histogram(db_path: str, measurement_id: int) -> pd.DataFrame:
    """Pull histogram bins for a single measurement."""
    engine = _engine(db_path)
    return pd.read_sql(
        """
        SELECT bin_index, bin_value, value
        FROM measurement_details
        WHERE measurement_id = ? AND detail_type = 'histogram_bin'
        ORDER BY bin_index
        """,
        engine,
        params=(measurement_id,),
    )


def get_all_measurements_for_device(db_path: str, device_name: str) -> pd.DataFrame:
    """All integrations across all runs that used a particular device."""
    engine = _engine(db_path)
    return pd.read_sql(
        """
        SELECT m.*, r.run_type, r.started_at as run_started
        FROM measurements m
        JOIN runs r ON m.run_id = r.id
        JOIN devices d ON r.device_id = d.id
        WHERE d.name = ?
        ORDER BY m.timestamp
        """,
        engine,
        params=(device_name,),
    )
