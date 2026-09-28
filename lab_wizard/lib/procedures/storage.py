"""Where procedures and params presets live in a workspace.

``config/procedures/<name>.yml``
    A procedure definition (see :mod:`lab_wizard.lib.procedures.definition`).

``lab_wizard/lib/procedures/library/<name>.yml``
    **Built-in** procedures that ship with lab_wizard. A workspace procedure of the same
    name takes precedence, so a lab can adapt a built-in without editing the
    package; saving always writes to the workspace.

``config/measurements/<measurement>/<preset>.yml``
    A named params preset — "the lab's standard PCR sweep" — for either a
    composed procedure or a hand-written measurement. This is the layer between
    a measurement's code defaults and a project's frozen copy that instruments
    already had and measurements did not. A project copies the preset's values
    when it is generated; editing the preset later changes no existing project.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel
from ruamel.yaml import YAML

from lab_wizard.lib.procedures.definition import ProcedureDefinition


__all__ = [
    "BUILTIN_DIR",
    "delete_procedure",
    "procedure_origin",
    "list_presets",
    "list_procedures",
    "load_preset",
    "load_procedure",
    "presets_dir",
    "procedures_dir",
    "save_preset",
    "save_procedure",
]


BUILTIN_DIR = Path(__file__).resolve().parent / "library"


def procedures_dir(config_dir: str | Path) -> Path:
    return Path(config_dir) / "procedures"


def presets_dir(config_dir: str | Path, measurement: str) -> Path:
    return Path(config_dir) / "measurements" / measurement


def _yaml() -> YAML:
    y = YAML(typ="rt")
    y.default_flow_style = False
    return y


def _check_file_name(name: str, what: str) -> None:
    if not name or not name.replace("-", "_").isidentifier():
        raise ValueError(f"{what} name {name!r} must be letters, digits, '_' or '-'")


# --------------------------- procedures ---------------------------


def _names(directory: Path) -> set[str]:
    return {p.stem for p in directory.glob("*.yml")} if directory.is_dir() else set()


def list_procedures(config_dir: str | Path) -> list[str]:
    """Every procedure available to this workspace: its own, and the built-ins."""
    return sorted(_names(procedures_dir(config_dir)) | _names(BUILTIN_DIR))


def procedure_origin(config_dir: str | Path, name: str) -> str | None:
    """``"workspace"``, ``"builtin"``, or ``None`` if there is no such procedure."""
    if (procedures_dir(config_dir) / f"{name}.yml").is_file():
        return "workspace"
    if (BUILTIN_DIR / f"{name}.yml").is_file():
        return "builtin"
    return None


def load_procedure(config_dir: str | Path, name: str) -> ProcedureDefinition:
    """A procedure by name — the workspace's own if it has one, else the built-in."""
    origin = procedure_origin(config_dir, name)
    if origin is None:
        known = ", ".join(list_procedures(config_dir)) or "none"
        raise ValueError(f"No procedure named {name!r} (have: {known})")
    path = (procedures_dir(config_dir) if origin == "workspace" else BUILTIN_DIR) / f"{name}.yml"
    data = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    definition = ProcedureDefinition.model_validate(data)
    if definition.name != name:
        raise ValueError(f"{path} defines procedure {definition.name!r}, but is named {name!r}")
    return definition


def save_procedure(config_dir: str | Path, definition: ProcedureDefinition) -> Path:
    """Write a procedure, refusing one that cannot be generated."""
    definition.check()
    path = procedures_dir(config_dir) / f"{definition.name}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = definition.model_dump(mode="json", exclude_none=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml().dump(payload, handle)
    return path


def delete_procedure(config_dir: str | Path, name: str) -> bool:
    """Delete a workspace procedure. A built-in cannot be deleted; deleting a
    workspace override brings the built-in back."""
    path = procedures_dir(config_dir) / f"{name}.yml"
    if not path.is_file():
        return False
    path.unlink()
    return True


# --------------------------- presets ---------------------------


def list_presets(config_dir: str | Path, measurement: str) -> list[str]:
    directory = presets_dir(config_dir, measurement)
    return sorted(p.stem for p in directory.glob("*.yml")) if directory.is_dir() else []


def load_preset(
    config_dir: str | Path, measurement: str, preset: str, model: type | None = None
) -> dict[str, Any]:
    """A preset's values, validated against ``model`` when one is given."""
    path = presets_dir(config_dir, measurement) / f"{preset}.yml"
    if not path.is_file():
        known = ", ".join(list_presets(config_dir, measurement)) or "none"
        raise ValueError(f"No preset {preset!r} for {measurement!r} (have: {known})")
    values = YAML(typ="safe").load(path.read_text(encoding="utf-8")) or {}
    if model is None or not issubclass(model, BaseModel):
        return values
    return model.model_validate(values).model_dump(mode="json")


def save_preset(
    config_dir: str | Path,
    measurement: str,
    preset: str,
    values: dict[str, Any],
    model: type[BaseModel],
) -> Path:
    """Validate ``values`` against ``model`` and write them, with field descriptions as comments."""
    from lab_wizard.lib.utilities.config_io import model_to_commented_map

    _check_file_name(measurement, "Measurement")
    _check_file_name(preset, "Preset")
    validated = model.model_validate(values)
    path = presets_dir(config_dir, measurement) / f"{preset}.yml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        _yaml().dump(model_to_commented_map(validated), handle)
    return path
