"""Custom measurements: Python files in a workspace's ``measurements/`` folder.

A procedure is a step tree composed in the wizard. A custom measurement is for
what a composed tree cannot say — a search that decides where to measure next,
a loop that stops on a condition, a step that talks to an instrument in its
own way. It is one Python file, ``<workspace>/measurements/<name>.py``, that
declares:

``Resources``
    a dataclass: one field per instrument role, typed by the behavior it needs
    (``VSource``, ``Counter``, ...), and a ``params`` field.
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
from types import ModuleType
from typing import Any, get_args, get_origin

from lab_procedure import Step
from pydantic import BaseModel

from lab_wizard.lib.procedures.codegen import class_prefix
from lab_wizard.lib.project_module import load_module
from lab_wizard.lib.recording import MeasureStep

__all__ = [
    "CustomMeasurement",
    "custom_setup_template_source",
    "list_custom_measurements",
    "load_custom_measurement",
    "measurement_procedure",
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
        raise ValueError(f"{path.name}: Resources must be a @dataclass")
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
    )


def measurement_procedure(module: ModuleType, resources: Any) -> Step:
    """The step tree for one run of a custom measurement module.

    A ``measure(resources, run)`` function runs as one step; a
    ``build_procedure(resources)`` returns its own tree.
    """
    measure = getattr(module, "measure", None)
    if measure is not None:
        return MeasureStep(measure, resources, name="measure")
    return module.build_procedure(resources)


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


def custom_setup_template_source(measurement: CustomMeasurement) -> str:
    """A setup template with ``wizard:`` blocks, for the project generator to fill.

    Unlike a procedure's, it declares no dataclass of its own: the measurement's
    ``Resources`` and ``Params`` are imported from the module copied beside it.
    """
    name = measurement.name
    prefix = class_prefix(name)
    return f'''"""
Setup for the custom measurement ``{name}``, generated by lab_wizard.

``_measurement/{name}.py`` is the measurement itself, copied from the
workspace's measurements folder; edit it freely. The project generator fills
the ``wizard:<block>`` regions with the selected instruments.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from lab_procedure import Status

from lab_wizard.lib.client.claims import RoutedClaims
from lab_wizard.lib.client.project_resources import local_claims_for, resource_source_for
from lab_wizard.lib.custom_measurements import measurement_procedure
from lab_wizard.lib.project_module import load_module, module_path
from lab_wizard.lib.task_adapters.lifecycle import RunLifecycle
from lab_wizard.lib.task_adapters.run import run_procedure
from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config

# The measurement is copied into _measurement/ beside this file, and loaded by
# its path so its name can never shadow another module (project_module.py).
# The import is only for your editor, to follow Params and Resources to their code.
if TYPE_CHECKING:
    import _measurement.{name} as {name}_module
    from _measurement.{name} import Params, Resources
else:
    {name}_module = load_module(module_path(Path(__file__).parent, "{name}"))
    Params, Resources = {name}_module.Params, {name}_module.Resources

# wizard:imports:start
# wizard inserts concrete instrument imports here
# wizard:imports:end

# Recorded with every run, so the Data page draws it with these plots.
{prefix.upper()}_DEFINITION = {{"name": "{name}", "plots": list(getattr({name}_module, "PLOTS", []))}}


def create_instrument_resources(
    project: ProjectConfig,
    resource_source: object | None = None,
) -> Resources:
    resources = resource_source or project.resources
    # wizard:instantiation:start
    # wizard inserts config-backed instrument construction here
    # wizard:instantiation:end

    return Resources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
        # wizard:return_fields:end
        params=Params.model_validate(project.measurement.params),
    )


def run_measurement(resources: Resources, project_dir: Path) -> Status:
    """Run once, recorded in the lab database with this project's run details."""
    return run_procedure(
        measurement_procedure({name}_module, resources),
        resources,
        procedure="{name}",
        definition={prefix.upper()}_DEFINITION,
        project_dir=project_dir,
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--remote",
        default=None,
        help="Route every instrument through one lab_wizard server "
        "(e.g. tcp://lab-server:12300).",
    )
    args = parser.parse_args()

    project_dir = Path(__file__).resolve().parent
    project = load_project_config(project_dir / f"{{project_dir.name}}.yaml")

    # Local instruments resolve against this workspace's config/instruments;
    # routed ones through their servers (resources.instrument_sources). Only
    # the local racks this project uses are claimed. See project_resources.py.
    resource_source = resource_source_for(project, project_dir, remote=args.remote)
    claims = local_claims_for(project, project_dir, owner=project_dir.name, remote=args.remote)

    # Claim local transports, build the instruments, claim the ones reached
    # through a server, reset them to their configured baseline, run, make them
    # safe if the run fails, release. See lifecycle.py.
    status = RunLifecycle(
        claims=claims,
        claims_after_resolve=[
            lambda instruments: RoutedClaims(instruments, holder=project_dir.name)
        ],
    ).run(
        resolve=lambda: create_instrument_resources(project, resource_source),
        execute=lambda resources: run_measurement(resources, project_dir),
    )
    raise SystemExit(0 if status is Status.SUCCESS else 1)
'''
