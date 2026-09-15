"""The registry of step types, for loading definitions and for building palettes.

Discovery is the instrument catalog's, under the ``step`` kind: any
``*StepParams`` class with a ``type`` literal in ``lib/procedures/steps/`` is
found by an AST scan and validated on first use. This module adds only what is
specific to steps — a description of each one's fields, for a composer to offer.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

from lab_wizard.lib.procedures.spec import ParamRef, RoleRef, StepParams, SweptRef
from lab_wizard.lib.utilities.resource_catalog import list_available_types, load_params_class


__all__ = ["step_params_class", "list_step_types", "step_catalog"]


def step_params_class(type_str: str) -> type[StepParams]:
    """The ``*StepParams`` class registered for ``type_str``."""
    try:
        return cast(type[StepParams], load_params_class(type_str, kind="step"))
    except KeyError as exc:
        known = ", ".join(list_step_types())
        raise ValueError(f"Unknown step type {type_str!r}. Known: {known}") from exc


def list_step_types() -> list[str]:
    return list_available_types("step")


def _field_kind(annotation: Any) -> str:
    text = repr(annotation)
    if "StepParams" in text:
        return "steps" if text.startswith("list[") else "step"
    if RoleRef.__name__ in text:
        return "role"
    if ParamRef.__name__ in text or SweptRef.__name__ in text:
        return "value"
    return "literal"


def step_catalog() -> dict[str, dict[str, Any]]:
    """What every step type is and takes, for a UI palette.

    ``fields`` describes each configurable field: whether it holds a step, a
    list of steps, a role (and which behaviors may fill it), a value (literal,
    param, or swept), or a plain literal.
    """
    out: dict[str, dict[str, Any]] = {}
    for type_str in list_step_types():
        cls = step_params_class(type_str)
        runtime = cls.step_class()
        requirements = cls.role_requirements()
        fields: dict[str, dict[str, Any]] = {}
        for name, info in cls.model_fields.items():
            if name == "type":
                continue
            fields[name] = {
                "kind": "role" if name in requirements else _field_kind(info.annotation),
                "requires": list(requirements.get(name, ())),
                "required": info.is_required(),
                "default": None if info.is_required() else info.get_default(call_default_factory=True),
            }
        doc = inspect.getdoc(cls) or ""
        out[type_str] = {
            "type": type_str,
            "params_class": f"{cls.__module__}.{cls.__name__}",
            "step_class": f"{runtime.__module__}.{runtime.__name__}",
            "summary": doc.splitlines()[0] if doc else "",
            "emits": list(cls.emits),
            "fields": fields,
        }
    return out
