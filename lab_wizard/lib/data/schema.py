"""The lab database: one SQLite file per workspace.

Only what every run or row has is a typed column; everything a procedure
records lives in one JSON ``values`` object per row, so no procedure ever
changes the schema. See ``plans/semantic_data_plan.md`` §3.

``setups`` holds each setup's current facts and the device mounted in it
(``plans/setup_plan.md``). A run copies its setup's fields when it starts, into
``runs.setup_fields``, with the measurement's bindings of its procedure's needs
to those fields in ``runs.setup_needs``; nothing reads a setup again for a past
run.

``plot_views`` is what part of a run's plot someone chose to look at (a
zoom kept on the Data page), by the plot's name; every view of the run draws
the plot that way.

``run_facets`` is derived from the other tables (the Data page's sidebar
filters on it) and can be rebuilt at any time; nothing treats it as the record.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

__all__ = ["DATABASE_NAME", "SCHEMA_VERSION", "DatabaseVersionError", "open_database"]

DATABASE_NAME = "lab.db"
SCHEMA_VERSION = 2

_TABLES = """
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE devices (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    properties TEXT NOT NULL DEFAULT '{}',
    notes      TEXT
);

CREATE TABLE setups (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE,
    notes     TEXT,
    fields    TEXT NOT NULL DEFAULT '{}',
    device_id INTEGER REFERENCES devices(id)
);

CREATE TABLE runs (
    id          INTEGER PRIMARY KEY,
    procedure   TEXT NOT NULL,
    status      TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    ended_at    TEXT,
    device_id   INTEGER REFERENCES devices(id),
    operator    TEXT,
    notes       TEXT,
    project     TEXT,
    setup        TEXT,
    setup_fields TEXT NOT NULL DEFAULT '{}',
    setup_needs  TEXT NOT NULL DEFAULT '{}',
    definition  TEXT,
    params      TEXT NOT NULL DEFAULT '{}',
    instruments TEXT NOT NULL DEFAULT '{}',
    columns     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX idx_runs_started ON runs(started_at);

CREATE TABLE steps (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    path       TEXT NOT NULL,
    kind       TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at   TEXT,
    status     TEXT,
    error      TEXT
);
CREATE INDEX idx_steps_run ON steps(run_id);

CREATE TABLE points (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    seq    INTEGER NOT NULL,
    t      TEXT NOT NULL,
    steps  TEXT NOT NULL,
    "values" TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE run_facets (
    run_id INTEGER NOT NULL REFERENCES runs(id),
    key    TEXT NOT NULL,
    value  TEXT NOT NULL,
    num    REAL,
    PRIMARY KEY (run_id, key, value)
);
CREATE INDEX idx_facets_value ON run_facets(key, value);
CREATE INDEX idx_facets_num ON run_facets(key, num);

CREATE TABLE plot_views (
    run_id  INTEGER NOT NULL REFERENCES runs(id),
    plot    TEXT NOT NULL,
    x_range TEXT,
    y_range TEXT,
    PRIMARY KEY (run_id, plot)
);
"""


class DatabaseVersionError(RuntimeError):
    """The file is not a lab database this version of lab_wizard can write."""


def open_database(path: str | Path) -> sqlite3.Connection:
    """Open the lab database at ``path``, creating it if it does not exist.

    Refuses a file written by a different schema version, or one that is not a
    lab database at all, rather than altering it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # The bus may deliver messages on a runner's worker thread; access is
    # still serialized, because a run emits from one thread at a time.
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    # With WAL, NORMAL survives a process crash; only an OS crash or power
    # loss can lose the last transaction.
    connection.execute("PRAGMA synchronous = NORMAL")

    tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        with connection:
            connection.executescript(_TABLES)
            connection.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        return connection

    version = None
    if "meta" in tables:
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        version = row["value"] if row else None
    if version != str(SCHEMA_VERSION):
        connection.close()
        found = f"schema version {version}" if version else "no lab_wizard schema version"
        raise DatabaseVersionError(
            f"{path} has {found}; this lab_wizard writes version {SCHEMA_VERSION}. "
            "It was not changed. Move it aside to start a new database."
        )
    return connection
