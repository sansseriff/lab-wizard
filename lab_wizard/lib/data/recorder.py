"""Record a run in the lab database as it happens.

The recorder is a sink on the run's two buses. ``RunStarted`` opens a ``runs``
row, each ``Point`` becomes a ``points`` row, each step's start and end become
a ``steps`` row, and ``RunEnded`` closes the run and derives its facets. Every
write is its own transaction, so a crash loses at most the row being recorded.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from lab_procedure import (
    MessageBus,
    Point,
    RunEnded,
    RunStarted,
    StepBegan,
    StepEnded,
)
from lab_procedure.messages import NodeId

from lab_wizard.lib.data.encoding import to_json
from lab_wizard.lib.data.facets import write_run_facets
from lab_wizard.lib.data.network import warn_if_networked
from lab_wizard.lib.data.schema import open_database
from lab_wizard.lib.utilities.process_lock import HeldLock, hold, is_held

__all__ = ["INTERRUPTED", "DatabaseRecorder", "run_lock_path", "settle_interrupted_runs"]

# The status of a run whose process ended without recording how the run ended.
INTERRUPTED = "interrupted"


def run_lock_path(db: str | Path, run_id: int) -> Path:
    """The file a run's process holds locked while it records into ``db``."""
    db = Path(db)
    return db.parent / ".running" / f"{db.stem}-{run_id}.lock"


def settle_interrupted_runs(connection: sqlite3.Connection, db: str | Path) -> list[int]:
    """Close every ``running`` run whose process is gone; returns their ids.

    Such a run ends ``interrupted``, at the last time it recorded anything, and
    so do its steps that never ended. Its facets are written then, as they
    would have been had it ended normally.
    """
    orphans = [
        run_id
        for (run_id,) in connection.execute("SELECT id FROM runs WHERE status = 'running'")
        if not is_held(run_lock_path(db, run_id))
    ]
    for run_id in orphans:
        (last,) = connection.execute(
            """SELECT MAX(t) FROM (
                   SELECT started_at AS t FROM runs WHERE id = :id
                   UNION ALL SELECT t FROM points WHERE run_id = :id
                   UNION ALL SELECT started_at FROM steps WHERE run_id = :id
                   UNION ALL SELECT ended_at FROM steps WHERE run_id = :id)""",
            {"id": run_id},
        ).fetchone()
        with connection:
            connection.execute(
                "UPDATE steps SET ended_at = ?, status = ? WHERE run_id = ? AND ended_at IS NULL",
                (last, INTERRUPTED, run_id),
            )
            connection.execute(
                "UPDATE runs SET status = ?, ended_at = ? WHERE id = ? AND status = 'running'",
                (INTERRUPTED, last, run_id),
            )
            write_run_facets(connection, run_id)
        run_lock_path(db, run_id).unlink(missing_ok=True)
        logger.warning("Run %s in %s stopped recording without ending; marked %s", run_id, db, INTERRUPTED)
    return orphans

logger = logging.getLogger(__name__)


class DatabaseRecorder:
    """Writes one run at a time into the lab database at ``path``."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        warn_if_networked(self.path)
        self.connection: sqlite3.Connection = open_database(self.path)
        self.run_id: int | None = None
        self._columns: dict[str, Any] = {}
        self._steps: dict[NodeId, list[int]] = {}
        self._lock: HeldLock | None = None

    def attach(self, data_bus: MessageBus, status_bus: MessageBus) -> None:
        data_bus.subscribe((RunStarted, Point, RunEnded), self.handle)
        status_bus.subscribe((StepBegan, StepEnded), self.handle)

    def close(self) -> None:
        self._release()
        self.connection.close()

    def _release(self) -> None:
        if self._lock is not None:
            self._lock.release()
            self._lock = None

    def handle(self, message: object) -> None:
        if isinstance(message, RunStarted):
            self._run_started(message)
        elif self.run_id is None:
            return  # nothing to attach it to
        elif isinstance(message, Point):
            self._point(message)
        elif isinstance(message, StepBegan):
            self._step_began(message)
        elif isinstance(message, StepEnded):
            self._step_ended(message)
        elif isinstance(message, RunEnded):
            self._run_ended(message)

    # ------------------------------------------------------------------

    def _device_id(self, name: str | None) -> int | None:
        if not name:
            return None
        self.connection.execute("INSERT OR IGNORE INTO devices (name) VALUES (?)", (name,))
        return self.connection.execute("SELECT id FROM devices WHERE name = ?", (name,)).fetchone()["id"]

    def _run_started(self, message: RunStarted) -> None:
        self._release()
        self._columns = dict(message.columns)
        self._steps = {}
        with self.connection:
            cursor = self.connection.execute(
                """INSERT INTO runs (procedure, status, started_at, device_id, operator, notes,
                                     project, metadata, definition, params, instruments, columns)
                   VALUES (?, 'running', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message.procedure,
                    message.t.isoformat(),
                    self._device_id(message.device),
                    message.operator or None,
                    message.notes or None,
                    message.project,
                    to_json(message.metadata),
                    to_json(message.definition) if message.definition is not None else None,
                    to_json(message.params),
                    to_json(message.instruments),
                    to_json(self._columns),
                ),
            )
            # Taken before the row is committed, so no reader ever sees this
            # run as running without its lock held.
            self._lock = hold(run_lock_path(self.path, cursor.lastrowid))
        self.run_id = cursor.lastrowid

    def _point(self, message: Point) -> None:
        # A hand-written measurement declares no columns; the ones it records
        # are learned as they arrive, and written as soon as they are, so a
        # run that is interrupted still says what it recorded.
        new = [name for name in message.values if name not in self._columns]
        for name in new:
            self._columns[name] = {"unit": None}
        with self.connection:
            self.connection.execute(
                'INSERT INTO points (run_id, seq, t, steps, "values") VALUES (?, ?, ?, ?, ?)',
                (self.run_id, message.seq, message.t.isoformat(), to_json(list(message.steps)), to_json(message.values)),
            )
            if new:
                self.connection.execute(
                    "UPDATE runs SET columns = ? WHERE id = ?", (to_json(self._columns), self.run_id)
                )

    def _step_began(self, message: StepBegan) -> None:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO steps (run_id, path, kind, started_at) VALUES (?, ?, ?, ?)",
                (self.run_id, "/".join(message.node_id), message.kind, message.t.isoformat()),
            )
        self._steps.setdefault(message.node_id, []).append(cursor.lastrowid)

    def _step_ended(self, message: StepEnded) -> None:
        pending = self._steps.get(message.node_id)
        if not pending:
            return
        step_id = pending.pop()
        with self.connection:
            self.connection.execute(
                "UPDATE steps SET ended_at = ?, status = ?, error = ? WHERE id = ?",
                (message.t.isoformat(), message.status, message.error, step_id),
            )

    def _run_ended(self, message: RunEnded) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE runs SET status = ?, ended_at = ?, columns = ? WHERE id = ?",
                (message.status, message.t.isoformat(), to_json(self._columns), self.run_id),
            )
            write_run_facets(self.connection, self.run_id)
        self._release()
        logger.info("Recorded run %s (%s) in %s", self.run_id, message.status, self.path)

    # ------------------------------------------------------------------

    def device(self, name: str) -> dict[str, Any]:
        """``{"name", "properties"}`` for a device, as recorded (empty properties if new)."""
        row = self.connection.execute("SELECT name, properties FROM devices WHERE name = ?", (name,)).fetchone()
        if row is None:
            return {"name": name, "properties": {}}
        return {"name": row["name"], "properties": json.loads(row["properties"] or "{}")}

    def run_row(self) -> dict[str, Any] | None:
        """The current run's ``runs`` row, JSON columns decoded. For tests and tools."""
        if self.run_id is None:
            return None
        row = dict(self.connection.execute("SELECT * FROM runs WHERE id = ?", (self.run_id,)).fetchone())
        for key in ("metadata", "definition", "params", "instruments", "columns"):
            row[key] = json.loads(row[key]) if row[key] else None
        return row
