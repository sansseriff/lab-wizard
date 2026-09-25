"""Reading the lab database back: find runs, list filters, load points.

For a notebook::

    from lab_wizard.lib.data import find
    runs = find(procedure="mcr_curve", **{"device.type": "SNSPD-A"})
    df = runs.points()          # polars: one row per point, with run_id

Filters name facet keys (``plans/semantic_data_plan.md`` §8) and match their
values exactly: a value, a list of values (any of them), or
``{"range": [lo, hi]}`` for a numeric facet. Different keys must all match.
Nobody writes SQL; it all lives here.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

from lab_wizard.lib.data.expressions import derive
from lab_wizard.lib.data.facets import facet_value
from lab_wizard.lib.data.schema import DATABASE_NAME, open_database
from lab_wizard.lib.workspace import find_workspace

__all__ = ["Lab", "Runs", "facets", "find", "lab_database"]

Filters = Mapping[str, Any]

_JSON_COLUMNS = ("metadata", "definition", "params", "instruments", "columns")


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
        open_database(self.path).close()  # refuses a wrong schema version up front

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
            "project": pl.String, "points": pl.Int64,
        }
        if not self.ids:
            return pl.DataFrame(schema=schema)
        where, params = self._where()
        rows = self.lab.query(
            f"""SELECT r.id, r.procedure, r.status, r.started_at, r.ended_at, d.name, r.operator,
                       r.notes, r.project, (SELECT COUNT(*) FROM points p WHERE p.run_id = r.id)
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

    def points(self, derived: Mapping[str, str] | None = None) -> pl.DataFrame:
        """One row per point: ``run_id``, ``seq``, ``t``, then every column.

        Columns come in the runs' declared order. A value a row did not record
        is null. ``derived`` adds computed columns (see ``expressions``).
        """
        if not self.ids:
            return pl.DataFrame(schema={"run_id": pl.Int64, "seq": pl.Int64, "t": pl.Datetime("us", "UTC")})
        where, params = self._where()
        names = list(self.columns())
        records = []
        for run_id, seq, t, values in self.lab.query(
            f'SELECT run_id, seq, t, "values" FROM points WHERE run_id IN {where} ORDER BY run_id, seq', params
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
        return derive(frame, derived) if derived else frame

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
