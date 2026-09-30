"""Custom measurements: Python files in a workspace's ``measurements/`` folder.

A procedure is a step tree composed in the wizard. A custom measurement is for
what a composed tree cannot say — a search that decides where to measure next,
a loop that stops on a condition, a step that talks to an instrument in its
own way. It is one Python file, ``<workspace>/measurements/<name>.py``, that
declares:

``Resources``
    a ``@dataclass(frozen=True)``: one field per instrument role, typed by the
    behavior it needs (``VSource``, ``Counter``, ...), and a ``params`` field.
    Frozen, because a project's setup file narrows each role to the class of
    the instrument it is bound to, and type checkers allow that only for a
    field that cannot be reassigned.
``Params``
    a pydantic model of the measurement's settings, with defaults.
``measure(resources, run)`` *or* ``build_procedure(resources) -> Step``
    what one run does. ``measure`` is plain Python — loops, ifs, any calls —
    recording each row with ``run.row(...)`` (:mod:`lab_wizard.lib.recording`).
    ``build_procedure`` returns a step tree instead, from ``lab_procedure`` and
    ``lab_wizard.lib.task_adapters.instrument_steps``, when the measurement is
    made of steps that already exist.
``PLOTS`` (optional)
    plot specs, as a procedure's ``plots:`` — what the Data page and a live
    plot draw for a run.

Its first docstring line is its description in the wizard. From there it is a
measurement like any other: the wizard binds its roles to instruments and
generates a project, and every run is recorded, saved and plotted the same
way. ``wizard init`` puts two examples in the folder.
"""

from __future__ import annotations

import dataclasses
import inspect
import keyword
import re
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from lab_wizard.lib.project_module import load_module

__all__ = [
    "CustomMeasurement",
    "list_custom_measurements",
    "load_custom_measurement",
]


@dataclass
class CustomMeasurement:
    """What a custom measurement file declares."""

    name: str
    path: Path
    description: str
    # {role: (behavior class, is_list)}
    roles: dict[str, tuple[type, bool]]
    params_model: type[BaseModel]
    plots: list[dict[str, Any]]
    # What one run calls: ``measure(resources, run)``, or ``build_procedure(resources)``.
    entry: Literal["measure", "build_procedure"] = "build_procedure"


def load_custom_measurement(path: str | Path) -> CustomMeasurement:
    """Read one custom measurement file; ``ValueError`` says what it is missing."""
    path = Path(path)
    if not path.stem.isidentifier() or keyword.iskeyword(path.stem):
        raise ValueError(
            f"{path.name}: a measurement's file name is its name in Python code, so it must be "
            "letters, digits and underscores, not starting with a digit — rename it, e.g. "
            f"{re.sub(r'\W', '_', path.stem).lstrip('0123456789') or 'measurement'}.py"
        )
    module = load_module(path)
    missing = [name for name in ("Resources", "Params") if not hasattr(module, name)]
    has_measure, has_build = hasattr(module, "measure"), hasattr(module, "build_procedure")
    if not (has_measure or has_build):
        missing.append("measure (or build_procedure)")
    if missing:
        raise ValueError(f"{path.name} does not define {', '.join(missing)}; see an example in this folder")
    if has_measure and has_build:
        raise ValueError(f"{path.name} defines both measure and build_procedure; a measurement is one or the other")
    resources_cls, params_model = module.Resources, module.Params
    if not dataclasses.is_dataclass(resources_cls):
        raise ValueError(f"{path.name}: Resources must be a @dataclass(frozen=True)")
    if not resources_cls.__dataclass_params__.frozen:  # type: ignore[attr-defined]
        # A project's setup file narrows each role to its instrument's class in a
        # subclass, and a type checker allows narrowing only a frozen field.
        raise ValueError(f"{path.name}: Resources must be @dataclass(frozen=True), not just @dataclass")
    if not (isinstance(params_model, type) and issubclass(params_model, BaseModel)):
        raise ValueError(f"{path.name}: Params must be a pydantic BaseModel")
    entry = "measure" if has_measure else "build_procedure"
    if not callable(getattr(module, entry)):
        raise ValueError(f"{path.name}: {entry} must be a function")

    hints = typing.get_type_hints(resources_cls)
    roles: dict[str, tuple[type, bool]] = {}
    for field in dataclasses.fields(resources_cls):
        if field.name == "params":
            continue
        annotation = hints.get(field.name)
        is_list = get_origin(annotation) in (list, tuple)
        behavior = (get_args(annotation) or (None,))[0] if is_list else annotation
        if not isinstance(behavior, type):
            raise ValueError(
                f"{path.name}: Resources.{field.name} must be typed by an instrument behavior "
                "such as VSource or Counter"
            )
        roles[field.name] = (behavior, is_list)

    doc = inspect.getdoc(module) or ""
    return CustomMeasurement(
        name=path.stem,
        path=path,
        description=doc.strip().splitlines()[0] if doc.strip() else "",
        roles=roles,
        params_model=params_model,
        plots=list(getattr(module, "PLOTS", []) or []),
        entry=entry,
    )


def list_custom_measurements(folder: str | Path | None) -> dict[str, CustomMeasurement | str]:
    """Every custom measurement in ``folder``, by name; a broken one maps to why."""
    out: dict[str, CustomMeasurement | str] = {}
    if folder is None or not Path(folder).is_dir():
        return out
    for path in sorted(Path(folder).glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            out[path.stem] = load_custom_measurement(path)
        except Exception as e:  # noqa: BLE001 - a broken file is listed with its error, not hidden
            out[path.stem] = f"{type(e).__name__}: {e}"
    return out
