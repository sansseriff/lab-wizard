"""What the Procedures section of the wizard reads and writes.

The composer edits a procedure definition as JSON — the same shape as the YAML
under ``config/procedures`` — and asks this module three things about it: what
it may contain (the step and behavior catalogs), what is wrong with it (every
problem, each with the path of the step it is about), and what it becomes (the
generated Python). Saving goes through :func:`save_procedure`, which refuses a
definition that does not check, so nothing the composer saves can fail to
generate. See ``plans/procedure_plan.md`` Phase 4.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from lab_procedure import ProcedureError, step_catalog

from lab_wizard.lib.procedures.catalog import behavior_catalog
from lab_wizard.lib.procedures.codegen import measurement_module_source
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import (
    BUILTIN_DIR,
    delete_procedure,
    list_presets,
    list_procedures,
    load_preset,
    load_procedure,
    presets_dir,
    procedure_origin,
    save_preset,
    save_procedure,
)
from lab_wizard.lib.utilities.python_formatting import format_python_code
from lab_wizard.lib.server.registry import InstrumentRegistry


logger = logging.getLogger("lab_wizard.wizard.backend.procedures_api")

__all__ = [
    "check_definition",
    "composer_catalog",
    "definition_from_yaml",
    "definition_to_yaml",
    "delete_workspace_procedure",
    "delete_procedure_preset",
    "procedure_detail",
    "procedure_presets",
    "procedure_summaries",
    "save_procedure_preset",
    "save_workspace_procedure",
]


def _problem(path: tuple[Any, ...], message: str) -> dict[str, Any]:
    return {"path": [p for p in path], "message": message}


def _validation_problems(exc: ValidationError) -> list[dict[str, Any]]:
    out = []
    for err in exc.errors():
        message = err["msg"].removeprefix("Value error, ")
        out.append(_problem(tuple(err["loc"]), message))
    return out


# --------------------------- catalog ---------------------------


def _fillers(config_dir: str | Path) -> dict[str, list[str]]:
    """``{behavior: [attribute names]}`` for this workspace's own instruments.

    What a role costs in portability is how few instruments can fill it, so the
    composer shows the count beside each role.
    """

    try:
        attributes = InstrumentRegistry.from_config_dir(str(config_dir)).list_descriptions()
    except Exception as exc:  # noqa: BLE001 - an unreadable tree fills nothing, it does not break the composer
        logger.warning("Could not index the local instrument tree: %s", exc)
        return {}
    behaviors = behavior_catalog()
    out: dict[str, list[str]] = {name: [] for name in behaviors}
    for attribute in attributes:
        declared = attribute.get("behavior_abc")
        name = attribute.get("attribute_name")
        if not declared or not name or declared not in behaviors:
            continue
        for satisfied in behaviors[declared]["satisfies"]:
            out[satisfied].append(name)
    return out


def composer_catalog(config_dir: str | Path) -> dict[str, Any]:
    return {"steps": step_catalog(), "behaviors": behavior_catalog(), "fillers": _fillers(config_dir)}


# --------------------------- reading ---------------------------


def procedure_summaries(config_dir: str | Path) -> list[dict[str, Any]]:
    """Every procedure, whether it loads and checks, and where it lives."""
    out: list[dict[str, Any]] = []
    for name in list_procedures(config_dir):
        origin = procedure_origin(config_dir, name)
        entry: dict[str, Any] = {
            "name": name,
            "origin": origin,
            "overrides_builtin": origin == "workspace" and (BUILTIN_DIR / f"{name}.yml").is_file(),
            "description": "",
            "roles": {},
            "records": [],
            "presets": list_presets(config_dir, name),
            "problems": [],
            "warnings": [],
        }
        try:
            definition = load_procedure(config_dir, name)
        except Exception as exc:  # noqa: BLE001 - listed with its error so it can be fixed
            entry["problems"] = [str(exc)]
            out.append(entry)
            continue
        entry["description"] = definition.description
        entry["roles"] = {role: decl.behavior for role, decl in definition.roles.items()}
        entry["records"] = definition.emitted_fields()
        entry["problems"] = [message for _path, message in definition.diagnose()]
        entry["warnings"] = [message for _path, message in definition.warnings()]
        out.append(entry)
    return out


def procedure_detail(config_dir: str | Path, name: str) -> dict[str, Any]:
    origin = procedure_origin(config_dir, name)
    if origin is None:
        raise KeyError(name)
    definition = load_procedure(config_dir, name)
    return {
        "name": name,
        "origin": origin,
        "has_builtin": (BUILTIN_DIR / f"{name}.yml").is_file(),
        "definition": definition.model_dump(mode="json", exclude_none=True),
    }


# --------------------------- checking ---------------------------


def check_definition(payload: dict[str, Any]) -> dict[str, Any]:
    """Everything wrong with ``payload``, and the Python it generates if nothing is.

    Never raises for a bad definition: the composer calls this on every edit,
    and a half-built tree is the normal state of one.
    """
    try:
        definition = ProcedureDefinition.model_validate(payload)
    except ValidationError as exc:
        return {"ok": False, "problems": _validation_problems(exc), "warnings": [], "records": [], "python": None}
    except ValueError as exc:
        return {"ok": False, "problems": [_problem((), str(exc))], "warnings": [], "records": [], "python": None}

    problems = [_problem(path, message) for path, message in definition.diagnose()]
    # The whole module a project gets, not just the tree: the roles bound as
    # locals and the params read are what make the tree legible.
    python = format_python_code(measurement_module_source(definition)) if not problems else None
    return {
        "ok": not problems,
        "problems": problems,
        # Things that generate and run, but probably not as intended.
        "warnings": [_problem(path, message) for path, message in definition.warnings()],
        "records": definition.emitted_fields(),
        "python": python,
    }


# --------------------------- writing ---------------------------


def save_workspace_procedure(config_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Save to ``config/procedures``; a built-in's name makes a workspace override.

    Raises :class:`ProcedureError` with located problems for anything that
    does not check, including a payload that does not validate.
    """
    try:
        definition = ProcedureDefinition.model_validate(payload)
    except ValidationError as exc:
        problems = _validation_problems(exc)
        raise ProcedureError(
            [p["message"] for p in problems], [(tuple(p["path"]), p["message"]) for p in problems]
        ) from exc
    path = save_procedure(config_dir, definition)
    logger.info("Saved procedure %s to %s", definition.name, path)
    return {"name": definition.name, "path": str(path), "origin": "workspace"}


def delete_workspace_procedure(config_dir: str | Path, name: str) -> dict[str, Any]:
    if not delete_procedure(config_dir, name):
        raise KeyError(name)
    logger.info("Deleted workspace procedure %s", name)
    return {"name": name, "origin": procedure_origin(config_dir, name)}


# --------------------------- YAML ---------------------------


def definition_to_yaml(payload: dict[str, Any]) -> str:
    """The composer's JSON as the YAML it is stored as, for hand editing."""
    y = YAML(typ="rt")
    y.default_flow_style = False
    buffer = io.StringIO()
    y.dump(payload, buffer)
    return buffer.getvalue()


def definition_from_yaml(text: str) -> dict[str, Any]:
    """Hand-edited YAML back to JSON. Only YAML syntax is checked here; the
    definition itself is checked like any other edit."""
    data = YAML(typ="safe").load(text)
    if not isinstance(data, dict):
        raise ValueError("A procedure definition is a mapping with name, roles, params and body")
    return data


# --------------------------- presets ---------------------------


def procedure_presets(config_dir: str | Path, name: str) -> dict[str, Any]:
    definition = load_procedure(config_dir, name)
    model = definition.params_model()
    presets: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for preset in list_presets(config_dir, name):
        try:
            presets[preset] = load_preset(config_dir, name, preset, model)
        except Exception as exc:  # noqa: BLE001 - a preset the params outgrew is shown, not dropped
            errors[preset] = str(exc)
    return {"defaults": definition.param_defaults(), "presets": presets, "errors": errors}


def save_procedure_preset(config_dir: str | Path, name: str, preset: str, values: dict[str, Any]) -> dict[str, Any]:
    definition = load_procedure(config_dir, name)
    path = save_preset(config_dir, name, preset, values, definition.params_model())
    logger.info("Saved preset %s for %s to %s", preset, name, path)
    return {"name": preset, "path": str(path)}


def delete_procedure_preset(config_dir: str | Path, name: str, preset: str) -> bool:
    path = presets_dir(config_dir, name) / f"{preset}.yml"
    if not path.is_file():
        return False
    path.unlink()
    return True
