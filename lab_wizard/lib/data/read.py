"""Reading the lab database back: find runs, list filters, load points.

For a notebook::

    from lab_wizard.lib.data import find
    runs = find(procedure="mcr_curve", **{"device.type": "SNSPD-A"})
    df = runs.points()          # polars: one row per point, with run_id

Filters name facet keys (``plans/semantic_data_plan.md`` §8) and match their
values exactly: a value, a list of values (any of them), or
``{"range": [lo, hi]}`` for a numeric facet, or for ``date`` with two ISO dates
(``{"range": ["2026-09-01", "2026-09-30"]}``). Different keys must all match.
Nobody writes SQL; it all lives here.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from lab_wizard.lib.data.expressions import derive
from lab_wizard.lib.data.facets import facet_value
from lab_wizard.lib.data.recorder import settle_interrupted_runs
from lab_wizard.lib.data.schema import DATABASE_NAME, open_database
from lab_wizard.lib.data.setups import resolve_needs
from lab_wizard.lib.workspace import find_workspace

logger = logging.getLogger(__name__)

__all__ = ["Lab", "Runs", "facets", "find", "lab_database"]

Filters = Mapping[str, Any]

_JSON_COLUMNS = ("setup_fields", "setup_needs", "definition", "params", "instruments", "columns")


def lab_database(start: str | Path | None = None) -> Path:
    """The lab database of the workspace containing ``start`` (default: here).

    Honours ``LAB_WIZARD_WORKSPACE``, so a notebook anywhere can name its lab.
    """
    workspace = find_workspace(start)
    if workspace is None:
        raise FileNotFoundError(
            "No lab-wizard.toml in this directory or its parents, and "
            "LAB_WIZARD_WORKSPACE is not set. Pass db= the path to a lab.db."
        )
    return workspace.data_dir / DATABASE_NAME


def _condition(key: str, wanted: Any) -> tuple[str, list[Any]]:
    """SQL selecting the ids of runs whose facet ``key`` matches ``wanted``."""
    base = "SELECT run_id FROM run_facets WHERE key = ?"
    if isinstance(wanted, Mapping):
        if set(wanted) != {"range"}:
            raise ValueError(f"filter {key!r}: a mapping must be {{'range': [lo, hi]}}, not {dict(wanted)!r}")
        lo, hi = wanted["range"]
        if isinstance(lo, str) and isinstance(hi, str):
            # Text bounds compare as text, which is date order for ISO dates.
            return f"{base} AND value BETWEEN ? AND ?", [key, lo, hi]
        return f"{base} AND num BETWEEN ? AND ?", [key, lo, hi]
    values = list(wanted) if isinstance(wanted, (list, tuple, set, frozenset)) else [wanted]
    texts = []
    for value in values:
        text = facet_value(value)
        if text is None:
            raise ValueError(f"filter {key!r}: {value!r} is not a value runs can be filtered by")
        texts.append(text[0])
    return f"{base} AND value IN ({', '.join('?' * len(texts))})", [key, *texts]


def _matching(filters: Filters) -> tuple[str, list[Any]]:
    """SQL selecting the ids of runs matching every filter."""
    if not filters:
        return "SELECT id FROM runs", []
    parts, params = [], []
    for key, wanted in filters.items():
        sql, args = _condition(key, wanted)
        parts.append(sql)
        params.extend(args)
    return " INTERSECT ".join(parts), params


class Lab:
    """One lab database, for reading.

    Each query opens and closes its own connection, so a ``Runs`` kept in a
    notebook for an afternoon holds nothing open, and sees runs recorded since.
    """

    def __init__(self, db: str | Path | None = None) -> None:
        self.path = Path(db) if db is not None else lab_database()
        if not self.path.is_file():
            raise FileNotFoundError(f"No lab database at {self.path}")
        connection = open_database(self.path)  # refuses a wrong schema version up front
        try:
            # A run whose process died reads as running until someone notices.
            settle_interrupted_runs(connection, self.path)
        except sqlite3.OperationalError:
            logger.warning("Could not close interrupted runs in %s (read-only?)", self.path, exc_info=True)
        finally:
            connection.close()

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        connection = open_database(self.path)
        try:
            return connection.execute(sql, list(params)).fetchall()
        finally:
            connection.close()

    def close(self) -> None:
        """Nothing is held open; kept so ``with Lab(...)`` reads naturally."""

    def __enter__(self) -> "Lab":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------ filters

    def facets(self, filters: Filters | None = None) -> pl.DataFrame:
        """Every filter the sidebar can offer, with how many runs each would leave.

        One row per ``(key, value)``. The count for a key's values ignores that
        key's own filter, so choosing ``procedure = mcr_curve`` still shows how
        many runs every other procedure has: the choices a sidebar offers.
        """
        filters = dict(filters or {})
        rows: list[tuple[Any, ...]] = []
        match_sql, match_params = _matching(filters)
        placeholders = ", ".join("?" * len(filters))
        rows += self.query(
            f"""SELECT key, value, MIN(num), COUNT(*) FROM run_facets
                WHERE run_id IN ({match_sql}) {f"AND key NOT IN ({placeholders})" if filters else ""}
                GROUP BY key, value""",
            [*match_params, *filters],
        )
        for key in filters:
            others = {k: v for k, v in filters.items() if k != key}
            sql, params = _matching(others)
            rows += self.query(
                f"""SELECT key, value, MIN(num), COUNT(*) FROM run_facets
                    WHERE key = ? AND run_id IN ({sql}) GROUP BY key, value""",
                [key, *params],
            )
        frame = pl.DataFrame(
            [tuple(r) for r in rows],
            schema={"key": pl.String, "value": pl.String, "num": pl.Float64, "runs": pl.Int64},
            orient="row",
        )
        return frame.sort(["key", "num", "value"], nulls_last=True)

    def find(self, filters: Filters | None = None, **by_key: Any) -> "Runs":
        """The runs matching every filter, newest first.

        Keys without a dot can be keywords: ``find(procedure="mcr_curve")``.
        Dotted ones go in the mapping: ``find({"device.type": "SNSPD-A"})``.
        """
        merged = {**(filters or {}), **by_key}
        sql, params = _matching(merged)
        ids = [
            r[0]
            for r in self.query(
                f"SELECT id FROM runs WHERE id IN ({sql}) ORDER BY started_at DESC, id DESC", params
            )
        ]
        return Runs(self, ids)

    def runs(self, ids: Iterable[int]) -> "Runs":
        ids = list(ids)
        found = {r[0] for r in self.query(
            f"SELECT id FROM runs WHERE id IN ({', '.join('?' * len(ids))})", ids
        )} if ids else set()
        missing = [i for i in ids if i not in found]
        if missing:
            raise KeyError(f"No run with id {', '.join(map(str, missing))} in {self.path}")
        return Runs(self, ids)


class Runs:
    """A set of runs, in a chosen order. Nothing is loaded until asked for."""

    def __init__(self, lab: Lab, ids: list[int]) -> None:
        self.lab = lab
        self.ids = ids

    def __len__(self) -> int:
        return len(self.ids)

    def __iter__(self):
        return iter(self.ids)

    def __repr__(self) -> str:
        return f"Runs({self.ids})"

    def _where(self) -> tuple[str, list[int]]:
        return f"({', '.join('?' * len(self.ids))})", list(self.ids)

    def table(self) -> pl.DataFrame:
        """One row per run: what the run list shows."""
        schema = {
            "id": pl.Int64, "procedure": pl.String, "status": pl.String, "started_at": pl.String,
            "ended_at": pl.String, "device": pl.String, "operator": pl.String, "notes": pl.String,
            "project": pl.String, "setup": pl.String, "points": pl.Int64,
        }
        if not self.ids:
            return pl.DataFrame(schema=schema)
        where, params = self._where()
        rows = self.lab.query(
            f"""SELECT r.id, r.procedure, r.status, r.started_at, r.ended_at, d.name, r.operator,
                       r.notes, r.project, r.setup, (SELECT COUNT(*) FROM points p WHERE p.run_id = r.id)
                FROM runs r LEFT JOIN devices d ON d.id = r.device_id
                WHERE r.id IN {where}""",
            params,
        )
        by_id = {r[0]: tuple(r) for r in rows}
        return pl.DataFrame([by_id[i] for i in self.ids], schema=schema, orient="row")

    def info(self, run_id: int) -> dict[str, Any]:
        """Everything recorded about one run, JSON decoded."""
        rows = self.lab.query(
            """SELECT r.*, d.name AS device FROM runs r LEFT JOIN devices d ON d.id = r.device_id
               WHERE r.id = ?""",
            (run_id,),
        )
        if not rows:
            raise KeyError(f"No run with id {run_id}")
        out = dict(rows[0])
        for key in _JSON_COLUMNS:
            out[key] = json.loads(out[key]) if out[key] else None
        return out

    def columns(self) -> dict[str, dict[str, Any]]:
        """``{name: {"unit": ...}}`` over all these runs, first run's order first."""
        out: dict[str, dict[str, Any]] = {}
        where, params = self._where()
        for (text,) in self.lab.query(f"SELECT columns FROM runs WHERE id IN {where}", params):
            for name, meta in json.loads(text or "{}").items():
                out.setdefault(name, meta)
        return out

    def params(self) -> dict[int, dict[str, Any]]:
        """``{run_id: params}``: what each run was asked to do."""
        if not self.ids:
            return {}
        where, ids = self._where()
        return {r[0]: json.loads(r[1] or "{}") for r in self.lab.query(f"SELECT id, params FROM runs WHERE id IN {where}", ids)}

    def setup_values(self) -> dict[int, dict[str, float]]:
        """``{run_id: {need: value}}``: what ``setup("…")`` reads in each run.

        Each need its procedure declared, read from the run's own copy of its
        setup through the run's own bindings, in the need's unit. A need that
        cannot be read is left out, so it reads as null.
        """
        if not self.ids:
            return {}
        where, ids = self._where()
        out: dict[int, dict[str, float]] = {}
        for run_id, fields, needs, definition in self.lab.query(
            f"SELECT id, setup_fields, setup_needs, definition FROM runs WHERE id IN {where}", ids
        ):
            declared = (json.loads(definition) if definition else {}).get("needs") or {}
            values, _problems = resolve_needs(json.loads(fields or "{}"), json.loads(needs or "{}"), declared)
            out[run_id] = values
        return out

    def derived_by_run(self) -> dict[int, dict[str, str]]:
        """``{run_id: {name: expression}}``: the ``derived:`` columns each run recorded.

        Each run is computed with its own, so two runs whose procedure changed
        a formula in between are each drawn as they were measured.
        """
        if not self.ids:
            return {}
        where, ids = self._where()
        return {
            run_id: dict(((json.loads(text) or {}).get("derived") or {}) if text else {})
            for run_id, text in self.lab.query(f"SELECT id, definition FROM runs WHERE id IN {where}", ids)
        }

    def derived(self) -> dict[str, str]:
        """The ``derived:`` columns the runs' procedures declare, first run's first."""
        out: dict[str, str] = {}
        if not self.ids:
            return out
        where, ids = self._where()
        for (text,) in self.lab.query(f"SELECT definition FROM runs WHERE id IN {where}", ids):
            for name, expression in ((json.loads(text) or {}).get("derived") or {}).items() if text else ():
                out.setdefault(name, expression)
        return out

    def points(self, derived: Mapping[str, str] | None = None, *, after: int = -1) -> pl.DataFrame:
        """One row per point: ``run_id``, ``seq``, ``t``, then every column.

        Columns come in the runs' declared order. A value a row did not record
        is null. ``derived`` adds computed columns (see ``expressions``); the
        runs' own ``derived:`` columns are added with ``points(self.derived())``.
        ``after`` reads only the points after that ``seq``: what a run being
        followed has recorded since it was last read.
        """
        if not self.ids:
            return pl.DataFrame(schema={"run_id": pl.Int64, "seq": pl.Int64, "t": pl.Datetime("us", "UTC")})
        where, params = self._where()
        names = list(self.columns())
        records = []
        for run_id, seq, t, values in self.lab.query(
            f'SELECT run_id, seq, t, "values" FROM points WHERE run_id IN {where} AND seq > ? ORDER BY run_id, seq',
            [*params, after],
        ):
            data = json.loads(values)
            for name in data:
                if name not in names:
                    names.append(name)
            records.append({"run_id": run_id, "seq": seq, "t": datetime.fromisoformat(t), **data})
        order = ["run_id", "seq", "t", *names]
        frame = pl.DataFrame(
            [{name: record.get(name) for name in order} for record in records],
            schema_overrides={"run_id": pl.Int64, "seq": pl.Int64},
            infer_schema_length=None,
            strict=False,
        )
        if frame.is_empty():
            frame = pl.DataFrame(schema={"run_id": pl.Int64, "seq": pl.Int64, "t": pl.Datetime("us", "UTC")})
        return derive(frame, derived, self.params(), self.setup_values()) if derived else frame

    def steps(self) -> pl.DataFrame:
        """Every step execution, in the order they started: the timeline."""
        schema = {
            "run_id": pl.Int64, "path": pl.String, "kind": pl.String, "started_at": pl.String,
            "ended_at": pl.String, "status": pl.String, "error": pl.String,
        }
        where, params = self._where()
        rows = self.lab.query(
            f"""SELECT run_id, path, kind, started_at, ended_at, status, error FROM steps
                WHERE run_id IN {where} ORDER BY run_id, id""",
            params,
        ) if self.ids else []
        return pl.DataFrame([tuple(r) for r in rows], schema=schema, orient="row")


def find(filters: Filters | None = None, *, db: str | Path | None = None, **by_key: Any) -> Runs:
    """:meth:`Lab.find` on the lab database of the current workspace, or ``db``."""
    return Lab(db).find(filters, **by_key)


def facets(filters: Filters | None = None, *, db: str | Path | None = None) -> pl.DataFrame:
    """:meth:`Lab.facets` on the lab database of the current workspace, or ``db``."""
    with Lab(db) as lab:
        return lab.facets(filters)
