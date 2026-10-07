"""A run's facts, flattened into the filters the Data page's sidebar offers.

Each facet is a ``(key, value, num)`` row: ``("device.type", "SNSPD-A", None)``,
``("param.readout.gate_time_s", "1.0", 1.0)``. They are generated from the run's
own record, so a new instrument type or param becomes a filter the first time a
run uses it, and a facet only ever describes the runs that have it. See
``plans/semantic_data_plan.md`` §8.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import Any

from lab_wizard.lib.data.units import in_base, to_base

__all__ = ["facet_value", "is_image", "is_quantity", "quantity_units", "run_facets", "write_run_facets"]

Facet = tuple[str, str, float | None]

# A value longer than this is prose or a blob, not something anyone filters by.
MAX_VALUE_LENGTH = 200


def facet_value(value: Any) -> tuple[str, float | None] | None:
    """``(text, number)`` for a value worth filtering by, else ``None``.

    Filters are matched against this same text, so ``1.0`` finds ``"1.0"``.
    """
    if isinstance(value, bool):
        return ("true" if value else "false"), None
    if isinstance(value, int):
        return str(value), float(value)
    if isinstance(value, float):
        return repr(value), value
    if isinstance(value, str) and value and len(value) <= MAX_VALUE_LENGTH:
        return value, None
    return None


def is_quantity(data: Any) -> bool:
    """Whether ``data`` is a value with a unit, ``{value: 1.2, unit: K}``: one leaf, not a group."""
    return isinstance(data, Mapping) and set(data) == {"value", "unit"} and isinstance(data["unit"], str)


def is_image(data: Any) -> bool:
    """Whether ``data`` is a picture, ``{image: "<sha256>.jpg"}``: a leaf, never a facet."""
    return isinstance(data, Mapping) and set(data) == {"image"} and isinstance(data["image"], str)


def quantity_units(data: Any, prefix: str = "setup") -> dict[str, str]:
    """``{"setup.bias_resistor": "Ω"}``: the unit each quantity's facet is in.

    Facets compare a quantity in its base unit (:func:`_leaves`), so that is the
    unit given: ``kΩ`` is filtered, and shown, as ``Ω``.
    """
    if is_quantity(data):
        return {prefix: to_base(data["unit"])[1]} if data["unit"] else {}
    out: dict[str, str] = {}
    if isinstance(data, Mapping) and not is_image(data):
        for key, value in data.items():
            out.update(quantity_units(value, f"{prefix}.{key}"))
    return out


def _leaves(prefix: str, data: Any) -> Iterator[Facet]:
    """One facet per scalar leaf of nested dicts; lists and pictures are skipped.

    A quantity (``{value, unit}``) is one leaf, filtered by its value in its
    base unit: 100 kΩ and 100000 Ω are one value, ``100000.0``.
    """
    if is_image(data):
        return
    if is_quantity(data):
        value = data["value"]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and data["unit"]:
            value = in_base(value, data["unit"])
        data = value
    if isinstance(data, Mapping):
        for key, value in data.items():
            yield from _leaves(f"{prefix}.{key}", value)
        return
    scalar = facet_value(data)
    if scalar is not None:
        yield prefix, scalar[0], scalar[1]


def run_facets(run: Mapping[str, Any], device: Mapping[str, Any] | None) -> list[Facet]:
    """Every facet of one run.

    ``run`` is a ``runs`` row with its JSON columns decoded; ``device`` is its
    ``devices`` row, likewise, or ``None``.
    """
    out: list[Facet] = []

    def add(key: str, value: Any) -> None:
        scalar = facet_value(value)
        if scalar is not None:
            out.append((key, scalar[0], scalar[1]))

    for key in ("procedure", "status", "operator", "project", "setup"):
        add(key, run.get(key))
    started = run.get("started_at")
    if started:
        # "Last Tuesday" is the lab's Tuesday, not UTC's.
        add("date", datetime.fromisoformat(started).astimezone().date().isoformat())

    if device is not None:
        add("device", device["name"])
        out.extend(_leaves("device", device.get("properties") or {}))

    out.extend(_leaves("setup", run.get("setup_fields") or {}))
    out.extend(_leaves("param", run.get("params") or {}))

    for role, snapshot in (run.get("instruments") or {}).items():
        if not isinstance(snapshot, Mapping):
            continue
        add(f"instrument.{role}.type", snapshot.get("type"))
        add(f"instrument.{role}.class", snapshot.get("class"))
        out.extend(_leaves(f"instrument.{role}", snapshot.get("params") or {}))

    for name in run.get("columns") or {}:
        add("column", name)

    # One run can name the same facet twice (a param and a column both "true");
    # the table's key is (run, key, value).
    return list(dict.fromkeys(out))


def write_run_facets(connection: sqlite3.Connection, run_id: int) -> None:
    """Replace one run's facets with ones derived from its current record."""
    row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if row is None:
        return
    run = dict(row)
    for key in ("setup_fields", "params", "instruments", "columns"):
        run[key] = json.loads(run[key]) if run[key] else {}
    device = None
    if run["device_id"] is not None:
        device_row = connection.execute("SELECT * FROM devices WHERE id = ?", (run["device_id"],)).fetchone()
        if device_row is not None:
            device = dict(device_row)
            device["properties"] = json.loads(device["properties"] or "{}")

    connection.execute("DELETE FROM run_facets WHERE run_id = ?", (run_id,))
    connection.executemany(
        "INSERT OR IGNORE INTO run_facets (run_id, key, value, num) VALUES (?, ?, ?, ?)",
        [(run_id, key, value, num) for key, value, num in run_facets(run, device)],
    )
