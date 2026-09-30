"""The embedded style: a setup file that says everything, and reads nothing.

A production project names its instruments and reads their settings, its
params and its run details from the workspace and its YAML when it runs. The
embedded style is the opposite, written to be read: every instrument's
settings, the measurement's params, the run's details and where it is recorded
are values in the setup file itself, and every instrument is constructed on
the page — the rack, the mainframe, the module — with its class named, so each
can be followed to its code. Nothing is looked up at run time; editing the
file is how it changes. Only the measurement's own code (its step tree, or a
custom measurement's ``measure``) stays in ``<name>_measurement.py``, as in every
project.

The file's shape::

    imports                       every class used, by name
    the measurement's code        from <name>_measurement.py, loaded by its path, with
                                  its params classes
    Resources                     one field per role, typed by its concrete class
    PARAMS, RUN, DATABASE, SINKS  the run's values
    <INSTRUMENT> = <X>Params(...) each instrument's settings, root first
    build_instruments()           construct them, parent to child
    run()                         run once, recorded
    __main__                      claim, build, baseline, run, safe state, release
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, get_origin

from pydantic import BaseModel

from lab_wizard.lib.custom_measurements import CustomMeasurement
from lab_wizard.lib.procedures.codegen import class_prefix, definition_block
from lab_wizard.lib.procedures.definition import ParamDecl, ParamTree, ProcedureDefinition, _SWEEP_ADAPTER
from lab_wizard.lib.procedures.spec import python_identifier
from lab_wizard.lib.utilities.model_tree import OutputsConfig
from lab_wizard.wizard.backend._generation_common import (
    _NodeRef,
    _node_lineage_leaf_to_root,
    _trim_channels_for_selection,
)

__all__ = ["Binding", "embedded_setup_source"]

Imports = set[tuple[str, str]]


@dataclass
class Binding:
    """One role and the instrument chosen for it: a tree node, and a channel of it or not."""

    role: str
    leaf: _NodeRef
    channel_index: int | None = None
    is_list: bool = False


# ------------------------------------------------------------------ values as Python


def _use(cls: type, imports: Imports, local: set[str]) -> str:
    """The name to write for ``cls``, importing it; a class of the measurement's own module is local."""
    if cls.__module__.startswith("lab_wizard_measurements."):
        local.add(cls.__name__)
    else:
        imports.add((cls.__module__, cls.__name__))
    return cls.__name__


def _is_discriminator(cls: type[BaseModel], name: str) -> bool:
    return name == "type" and get_origin(cls.model_fields[name].annotation) is Literal


def _literal(value: Any, imports: Imports, local: set[str]) -> str:
    """``value`` as the Python that constructs it: models by their class, the rest as literals."""
    if isinstance(value, BaseModel):
        cls = type(value)
        args = [
            f"{name}={_literal(getattr(value, name), imports, local)}"
            for name in cls.model_fields
            if name != "children" and not _is_discriminator(cls, name)
        ]
        return f"{_use(cls, imports, local)}({', '.join(args)})"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_literal(k, imports, local)}: {_literal(v, imports, local)}" for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(v, imports, local) for v in value) + "]"
    if isinstance(value, tuple):
        return "(" + "".join(f"{_literal(v, imports, local)}, " for v in value) + ")"
    if isinstance(value, Path):
        imports.add(("pathlib", "Path"))
        return f"Path({str(value)!r})"
    return repr(value)


def _model_block(name: str, model: BaseModel, imports: Imports, local: set[str]) -> str:
    """``NAME = XParams(`` one field a line, each with its description as a comment ``)``."""
    cls = type(model)
    lines = [f"{name} = {_use(cls, imports, local)}("]
    for field_name, info in cls.model_fields.items():
        if field_name == "children" or _is_discriminator(cls, field_name):
            continue
        line = f"    {field_name}={_literal(getattr(model, field_name), imports, local)},"
        if info.description:
            line += f"  # {' '.join(info.description.split())}"
        lines.append(line)
    lines.append(")")
    return "\n".join(lines)


def _param_classes(tree: ParamTree, class_name: str) -> list[str]:
    """The names ``params_models_source`` gives a param tree's classes, root first."""
    names = [class_name]
    for name, entry in tree.entries.items():
        if isinstance(entry, ParamTree):
            names += _param_classes(entry, f"{class_name[: -len('Params')]}{class_prefix(name)}Params")
    return names


def _tree_literal(tree: ParamTree, values: dict[str, Any], class_name: str, imports: Imports, local: set[str]) -> str:
    """A procedure's params, constructed from the classes ``params_models_source`` generates."""
    args = []
    for name, entry in tree.entries.items():
        if isinstance(entry, ParamTree):
            sub = f"{class_name[: -len('Params')]}{class_prefix(name)}Params"
            args.append(f"{name}={_tree_literal(entry, values.get(name) or {}, sub, imports, local)}")
        else:
            assert isinstance(entry, ParamDecl)
            raw = values.get(name, entry.default)
            value = _SWEEP_ADAPTER.validate_python(raw) if entry.type == "sweep" else raw
            args.append(f"{name}={_literal(value, imports, local)}")
    return f"{class_name}({', '.join(args)})"


# ------------------------------------------------------------------ instruments


@dataclass
class _Built:
    """One instrument to construct: its variable, its settings' constant, and how."""

    var: str
    const: str
    node: _NodeRef
    parent: "_Built | None"


def _instruments(bindings: list[Binding]) -> tuple[list[_Built], dict[str, str]]:
    """Every instrument the roles need, root first, each once; and each role's expression."""
    built: dict[tuple[tuple[str, str], ...], _Built] = {}
    names: dict[str, int] = {}
    roles: dict[str, str] = {}
    for binding in bindings:
        chain = list(reversed(_node_lineage_leaf_to_root(binding.leaf)))
        lineage: tuple[tuple[str, str], ...] = ()
        parent: _Built | None = None
        for node in chain:
            lineage = (*lineage, (node.type, node.key))
            if lineage not in built:
                base = python_identifier(node.type).lower()
                names[base] = names.get(base, 0) + 1
                var = base if names[base] == 1 else f"{base}_{names[base]}"
                built[lineage] = _Built(var=var, const=var.upper(), node=node, parent=parent)
            parent = built[lineage]
        assert parent is not None
        expr = parent.var if binding.channel_index is None else f"{parent.var}.channels[{binding.channel_index}]"
        roles[binding.role] = f"[{expr}]" if binding.is_list else expr
    return list(built.values()), roles


def _channels_used(bindings: list[Binding]) -> dict[int, set[int]]:
    """For each channel provider (by node identity), the channels the roles use."""
    used: dict[int, set[int]] = {}
    for b in bindings:
        if b.channel_index is not None:
            used.setdefault(id(b.leaf.params), set()).add(b.channel_index)
    return used


def _resource_class(node: _NodeRef) -> type:
    return type(node.params).resource_class()


def _role_type(binding: Binding) -> type:
    cls = _resource_class(binding.leaf)
    return cls.channel_class if binding.channel_index is not None else cls  # type: ignore[attr-defined]


# ------------------------------------------------------------------ the file


def embedded_setup_source(
    *,
    measurement: ProcedureDefinition | CustomMeasurement,
    bindings: list[Binding],
    params: dict[str, Any],
    outputs: OutputsConfig,
    database: Path,
) -> str:
    """The whole setup file of an embedded-style project (unformatted; black it)."""
    imports: Imports = {
        ("dataclasses", "dataclass"),
        ("pathlib", "Path"),
        ("typing", "TYPE_CHECKING"),
        ("lab_procedure", "Status"),
        ("lab_wizard.lib.client.local_claims", "LocalTransportClaim"),
        ("lab_wizard.lib.project_module", "load_module"),
        ("lab_wizard.lib.project_module", "module_path"),
        ("lab_wizard.lib.task_adapters.lifecycle", "RunLifecycle"),
        ("lab_wizard.lib.task_adapters.run", "run_procedure"),
        ("lab_wizard.lib.utilities.model_tree", "RunConfig"),
    }
    local: set[str] = set()
    name = measurement.name

    # The measurement's code, and its params.
    if isinstance(measurement, ProcedureDefinition):
        prefix = class_prefix(name)
        build_fn = f"build_{python_identifier(name)}_procedure"
        params_class = f"{prefix}Params"
        # The params classes live beside the step tree; PARAMS below is this run's values.
        from_module = [build_fn, *_param_classes(measurement.param_tree, params_class)]
        imports.add(("typing", "Any"))
        definition_source = (
            f"# The procedure this run records, as the step tree in {name}_measurement.py was built from it.\n"
            + definition_block(measurement)
            + "\n\n"
        )
        values = measurement.params_model().model_validate(params).model_dump()
        params_value = _tree_literal(measurement.param_tree, values, params_class, imports, local)
        resources_class = f"{prefix}Resources"
        procedure_expr = f"{build_fn}(resources)"
        definition_expr = "DEFINITION"
    else:
        from_module = ["Params", "Resources"]
        definition_source = ""
        params_class = "Params"
        params_value = _literal(measurement.params_model.model_validate(params), imports, local)
        resources_class = "Resources"
        if measurement.entry == "measure":
            imports.add(("lab_wizard.lib.recording", "MeasureStep"))
            from_module.append("measure")
            procedure_expr = 'MeasureStep(measure, resources, name="measure")'
        else:
            from_module.append("build_procedure")
            procedure_expr = "build_procedure(resources)"
        if measurement.plots:
            from_module.append("PLOTS")
        definition_expr = f'{{"name": {name!r}, "plots": {"PLOTS" if measurement.plots else "[]"}}}'

    # The instruments: settings, then construction.
    built, role_exprs = _instruments(bindings)
    channels = _channels_used(bindings)
    settings_blocks = []
    construct_lines = []
    for item in built:
        node_params = _trim_channels_for_selection(item.node.params.model_copy(deep=True), channels.get(id(item.node.params)))
        settings_blocks.append(_model_block(item.const, node_params, imports, local))
        cls_name = _use(_resource_class(item.node), imports, local)
        if item.parent is None:
            construct_lines.append(f"{item.var} = {cls_name}.from_params({item.const})")
        else:
            construct_lines.append(f"{item.var} = {cls_name}.from_parent({item.parent.var}, {item.const})")
    roots = [item for item in built if item.parent is None]

    role_fields = []
    for binding in bindings:
        type_name = _use(_role_type(binding), imports, local)
        role_fields.append(f"    {binding.role}: {f'list[{type_name}]' if binding.is_list else type_name}")
    resources_source = ""
    if isinstance(measurement, ProcedureDefinition):
        resources_source = (
            "@dataclass\n"
            f"class {resources_class}:\n"
            f'    """The instruments this run drives, as the classes they are, and its params."""\n\n'
            + "\n".join(role_fields)
            + f"\n    params: {params_class}\n"
        )

    sinks = []
    if outputs.files:
        imports.add(("lab_wizard.lib.savers", "FileSaver"))
        sinks.append("FileSaver()")
    if outputs.live_plot == "window":
        imports.add(("lab_wizard.lib.plotters", "MplPlotter"))
        sinks.append(f"MplPlotter(plot={outputs.plot!r})")
    elif outputs.live_plot == "web":
        imports.add(("lab_wizard.lib.plotters", "WebPlotter"))
        sinks.append(f"WebPlotter(plot={outputs.plot!r})")

    measurement_names = sorted(set(from_module) | local)
    by_module: dict[str, set[str]] = {}
    for module, item in imports:
        by_module.setdefault(module, set()).add(item)
    def group(module: str) -> int:
        top = module.split(".")[0]
        return 0 if top in sys.stdlib_module_names else 2 if top == "lab_wizard" else 1

    import_lines = "\n\n".join(
        "\n".join(f"from {m} import {', '.join(sorted(n))}" for m, n in sorted(by_module.items()) if group(m) == g)
        for g in (0, 1, 2)
    )
    unpack = ", ".join(measurement_names)
    runtime_unpack = "\n    ".join(f"{n} = _module.{n}" for n in measurement_names)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    role_args = "\n".join(f"        {role}={expr}," for role, expr in role_exprs.items())
    claim_map = ", ".join(f"{item.var!r}: {item.const}" for item in roots)

    return f'''"""
``{name}``, embedded: everything this run uses is written in this file.

Generated by lab_wizard on {stamp} from the workspace's configuration. Nothing
below is read from the workspace, or from this project's YAML, when it runs:
the instruments' settings, the params, the run's details and where it is
recorded are all values here, so later changes to the workspace do not reach
this file. Edit the values below instead.

Only the measurement's code — what one run does — is in
``{name}_measurement.py``, as in every project.
"""

{import_lines}

# The measurement's code, in {name}_measurement.py beside this file. It is
# loaded by its path, so its name can never shadow another module
# (project_module.py); the import is only for your editor, to follow it.
if TYPE_CHECKING:
    from {name}_measurement import {unpack}
else:
    _module = load_module(module_path(Path(__file__).parent, {name!r}))
    {runtime_unpack}


{resources_source}


# ---------------------------------------------------------------- this run

{definition_source}PARAMS = {params_value}

# Who and what the run is about; recorded with it, and filters on the Data page.
RUN = RunConfig(device=None, operator=None, notes=None, metadata={{}})

# The lab database the run is recorded in.
DATABASE = Path({str(database)!r})

# What the run produces besides its database record.
SINKS = [{", ".join(sinks)}]


# ---------------------------------------------------------------- the instruments
# Each instrument's settings, as config/instruments had them when this file was
# generated, root first.

{chr(10).join(settings_blocks)}


def build_instruments() -> {resources_class}:
    """Open each instrument, parent before child, and hand the run the ones it uses."""
{textwrap.indent(chr(10).join(construct_lines), "    ")}
    return {resources_class}(
{role_args}
        params=PARAMS,
    )


def run(resources: {resources_class}) -> Status:
    """Run once, recorded in DATABASE."""
    return run_procedure(
        {procedure_expr},
        resources,
        procedure={name!r},
        definition={definition_expr},
        project_dir=Path(__file__).resolve().parent,
        run=RUN,
        database=DATABASE,
        sinks=SINKS,
    )


if __name__ == "__main__":
    # Claim the transports this run opens, build the instruments, reset each to
    # the baseline in its settings, run, put them in their safe state if the run
    # fails, release. See lab_wizard/lib/task_adapters/lifecycle.py.
    status = RunLifecycle(
        claims=[LocalTransportClaim({{{claim_map}}}, owner=Path(__file__).resolve().parent.name)],
    ).run(resolve=build_instruments, execute=run)
    raise SystemExit(0 if status is Status.SUCCESS else 1)
'''
