"""What every registered step type is and takes, for a GUI to offer."""

from __future__ import annotations

import inspect
from typing import Any

from lab_procedure.schema import ParamRef, RoleRef, StepParams, SweptRef, list_step_types, step_params_class


__all__ = ["step_catalog"]


def _field_kind(annotation: Any) -> str:
    text = repr(annotation)
    if "StepParams" in text:
        return "steps" if text.startswith("list[") else "step"
    if RoleRef.__name__ in text:
        return "role"
    if text.startswith("dict[") and ParamRef.__name__ in text:
        return "value_map"
    if ParamRef.__name__ in text and SweptRef.__name__ not in text:
        # A sweep's values: a sweep param, or a literal list.
        return "values"
    if ParamRef.__name__ in text or SweptRef.__name__ in text:
        return "value"
    return "literal"


def _literal_type(annotation: Any) -> str:
    return {bool: "bool", int: "int", float: "float", str: "str"}.get(annotation, "any")


def _group(cls: type[StepParams]) -> str:
    """``instruments`` for steps that drive a behavior, ``flow`` for the rest."""
    return "instruments" if cls.role_requirements() or any(
        _field_kind(info.annotation) == "role" for info in cls.model_fields.values()
    ) else "flow"


def step_catalog() -> dict[str, dict[str, Any]]:
    """What every registered step type is and takes, for a palette.

    ``fields`` describes each configurable field by ``kind``:

    ``step`` / ``steps``
        one child step (``optional`` if it may be left empty), or a list
    ``role``
        an instrument role; ``requires`` lists the behaviors that may fill it,
        empty meaning any
    ``value``
        a literal, ``{param: …}``, or ``{swept: …}``
    ``values``
        a sweep param or a literal list of numbers
    ``value_map``
        a mapping of setting names to values
    ``literal``
        a plain ``literal_type`` value; ``column`` is ``records`` when it names
        a data column this step writes, ``reads`` when it names one it reads
    """
    out: dict[str, dict[str, Any]] = {}
    for type_str in list_step_types():
        cls = step_params_class(type_str)
        runtime = cls.step_class()
        requirements = cls.role_requirements()
        fields: dict[str, dict[str, Any]] = {}
        for name, info in cls.model_fields.items():
            if name in ("type", "name"):
                continue
            kind = "role" if name in requirements else _field_kind(info.annotation)
            extra = info.json_schema_extra if isinstance(info.json_schema_extra, dict) else {}
            default = None if info.is_required() else info.get_default(call_default_factory=True)
            fields[name] = {
                "kind": kind,
                "requires": list(requirements.get(name, ())),
                "required": info.is_required(),
                "optional": not info.is_required() and default is None,
                "default": default,
                "literal_type": _literal_type(info.annotation) if kind == "literal" else None,
                "column": extra.get("column"),
            }
        doc = inspect.getdoc(cls) or ""
        out[type_str] = {
            "type": type_str,
            "group": _group(cls),
            "params_class": f"{cls.__module__}.{cls.__name__}",
            "step_class": f"{runtime.__module__}.{runtime.__name__}",
            "summary": doc.splitlines()[0] if doc else "",
            "doc": doc,
            "emits": list(cls.emits),
            "fields": fields,
        }
    return out
