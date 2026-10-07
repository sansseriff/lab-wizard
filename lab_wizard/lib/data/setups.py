"""Setups: what an experiment is, now, and what a run needs from it.

A setup is a named, long-lived description of one experiment
(``plans/setup_plan.md``): free-form fields, and the device mounted in it.
Fields nest in groups; a leaf is text, a number, true/false, a quantity
(``{value: 100, unit: kΩ}``), or a picture (``{image: "<sha256>.jpg"}``, or a
list of them), whose file is in ``<data_dir>/setup_images/``.

A run copies its setup's fields when it starts (:func:`setup_for_run`), so
nothing here is read again for a past run. A procedure that needs a fact to
draw its plots declares a *need* (``bias_resistance: {unit: ohm}``); a
measurement binds it to one of its setup's fields, and :func:`resolve_needs`
reads the value, in the need's unit, from a copy of the fields.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lab_wizard.lib.data.facets import is_image, is_quantity
from lab_wizard.lib.data.units import UnitError, base_unit, convert, in_base

__all__ = [
    "IMAGES_DIR",
    "SetupError",
    "check_fields",
    "delete_setup",
    "field_at",
    "field_leaves",
    "get_setup",
    "list_setups",
    "need_value",
    "resolve_needs",
    "save_image",
    "save_setup",
    "setup_for_run",
]

# Where a setup's pictures are kept, beside the lab database.
IMAGES_DIR = "setup_images"
_IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}


class SetupError(ValueError):
    """A setup, a field or a need that is not usable as given; says which."""


# --------------------------- fields ---------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def check_fields(fields: Any, prefix: str = "") -> None:
    """Refuse ``fields`` unless every name and leaf is one a setup can hold."""
    if not isinstance(fields, Mapping):
        raise SetupError(f"{prefix or 'fields'}: a group of named fields")
    for key, value in fields.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if not isinstance(key, str) or not key.strip() or "." in key:
            raise SetupError(f"{path!r}: a field needs a name without dots, like bias_resistor")
        if value is None or isinstance(value, (str, bool)) or _is_number(value):
            continue
        if is_quantity(value):
            if not (value["value"] is None or _is_number(value["value"])):
                raise SetupError(f"{path}: a quantity's value is a number")
            continue
        if is_image(value) or (isinstance(value, list) and all(is_image(v) for v in value)):
            continue
        if isinstance(value, Mapping):
            check_fields(value, path)
            continue
        raise SetupError(f"{path}: text, a number, true/false, a quantity, a picture, or a group")


def field_at(fields: Mapping[str, Any], path: str) -> Any:
    """The field at the dotted ``path``, or ``None`` if there is none."""
    node: Any = fields
    for part in path.split("."):
        if not isinstance(node, Mapping) or is_quantity(node) or part not in node:
            return None
        node = node[part]
    return node


def field_leaves(fields: Mapping[str, Any], prefix: str = "") -> list[dict[str, Any]]:
    """Every leaf of ``fields``, in order: what a need can be bound to.

    Each is ``{path, value, unit, base_unit, number}``: ``number`` is the
    value in its base unit if it is a number, else ``None``.
    """
    out: list[dict[str, Any]] = []
    for key, value in fields.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping) and not is_quantity(value) and not is_image(value):
            out.extend(field_leaves(value, path))
            continue
        unit = value["unit"] if is_quantity(value) else None
        raw = value["value"] if is_quantity(value) else value
        out.append({
            "path": path,
            "value": value,
            "unit": unit or None,
            "base_unit": base_unit(unit) if unit else None,
            "number": in_base(raw, unit) if _is_number(raw) else None,
        })
    return out


def need_value(fields: Mapping[str, Any], path: str, unit: str | None) -> float:
    """The number at ``path``, in ``unit``; ``SetupError`` saying why it cannot be."""
    value = field_at(fields, path)
    if value is None:
        raise SetupError(f"the setup has no field {path}")
    have = None
    if is_quantity(value):
        value, have = value["value"], value["unit"]
    if not _is_number(value):
        raise SetupError(f"{path} is not a number")
    try:
        return convert(value, have, unit)
    except UnitError:
        raise SetupError(f"{path} is in {have}, which is not {unit}") from None


def resolve_needs(
    fields: Mapping[str, Any],
    bindings: Mapping[str, str],
    declared: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, float], dict[str, str]]:
    """Each declared need's value from ``fields``, and why any could not be read.

    ``declared`` is a procedure's ``needs:`` (``{name: {unit, description}}``),
    ``bindings`` the measurement's ``{need: field path}``.
    """
    values: dict[str, float] = {}
    problems: dict[str, str] = {}
    for name, decl in declared.items():
        path = bindings.get(name)
        if not path:
            problems[name] = f"{name} is not bound to a setup field"
            continue
        try:
            values[name] = need_value(fields, path, (decl or {}).get("unit"))
        except SetupError as e:
            problems[name] = f"{name}: {e}"
    return values, problems


# --------------------------- the setups table ---------------------------


def _row(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    device = None
    if row["device_id"] is not None:
        found = connection.execute("SELECT name FROM devices WHERE id = ?", (row["device_id"],)).fetchone()
        device = found["name"] if found else None
    return {"name": row["name"], "notes": row["notes"], "fields": json.loads(row["fields"] or "{}"), "device": device}


def list_setups(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every setup, by name, with how many runs were taken on it and when the last was."""
    out = []
    for row in connection.execute("SELECT * FROM setups ORDER BY name"):
        setup = _row(connection, row)
        count, last = connection.execute(
            "SELECT COUNT(*), MAX(started_at) FROM runs WHERE setup = ?", (row["name"],)
        ).fetchone()
        out.append({**setup, "runs": count, "last_run": last})
    return out


def get_setup(connection: sqlite3.Connection, name: str) -> dict[str, Any]:
    """One setup; ``KeyError`` if there is none of that name."""
    row = connection.execute("SELECT * FROM setups WHERE name = ?", (name,)).fetchone()
    if row is None:
        raise KeyError(f"No setup named {name!r}")
    return _row(connection, row)


def save_setup(
    connection: sqlite3.Connection,
    name: str,
    fields: Mapping[str, Any],
    *,
    device: str | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Create or replace a setup. The device must be one the lab has registered."""
    name = name.strip()
    if not name or "/" in name:
        raise SetupError("a setup needs a name, like mid-ir-bench")
    check_fields(fields)
    device_id = None
    if device:
        found = connection.execute("SELECT id FROM devices WHERE name = ?", (device,)).fetchone()
        if found is None:
            raise SetupError(f"no device named {device!r}; register it under Data → Devices first")
        device_id = found["id"]
    with connection:
        connection.execute(
            """INSERT INTO setups (name, notes, fields, device_id) VALUES (?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET notes = excluded.notes, fields = excluded.fields,
                                               device_id = excluded.device_id""",
            (name, notes, json.dumps(dict(fields)), device_id),
        )
    return get_setup(connection, name)


def delete_setup(connection: sqlite3.Connection, name: str) -> None:
    """Forget a setup. Its runs keep their copies of it."""
    get_setup(connection, name)
    with connection:
        connection.execute("DELETE FROM setups WHERE name = ?", (name,))


def setup_for_run(connection: sqlite3.Connection, name: str | None) -> tuple[dict[str, Any], str | None]:
    """``(fields, device)``: what a run on setup ``name`` copies when it starts."""
    if not name:
        return {}, None
    try:
        setup = get_setup(connection, name)
    except KeyError:
        raise SetupError(f"no setup named {name!r}; create it on the Setups page") from None
    return setup["fields"], setup["device"]


# --------------------------- pictures ---------------------------


def save_image(data_dir: Path, content: bytes, suffix: str) -> str:
    """Keep a picture, named by what is in it; returns that name.

    The same picture saved twice is one file. Files are never changed, so a
    run's copy of its setup always shows the picture it was taken with.
    """
    suffix = suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
    if suffix not in _IMAGE_TYPES:
        raise SetupError(f"a picture is one of {', '.join(sorted(_IMAGE_TYPES))}, not {suffix}")
    name = hashlib.sha256(content).hexdigest() + suffix
    folder = Path(data_dir) / IMAGES_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    if not path.exists():
        path.write_bytes(content)
    return name
