"""Turn a procedure definition into the Python a generated project runs.

Two files, matching a hand-written measurement so the project generator treats
both the same way:

* ``<name>.py`` — ``build_<name>_procedure()`` and a ``<Name>Measurement`` class.
  The step tree sits between ``# wizard:procedure:start`` / ``:end`` markers,
  so it can be regenerated without touching anything edited around it.
* a setup *template* — the resources dataclass (one field per role, typed by its
  behavior), the params models, and the same ``__main__`` run lifecycle as every
  other project. The project generator fills its ``wizard:`` blocks with the
  selected instruments.

The params models live in the setup file, not the measurement module, because
the measurement module only reads ``resources.params`` and the setup file is
the one that validates the project YAML against them.

Output is valid but unformatted; the wizard backend formats it with black.
"""

from __future__ import annotations

from typing import Any

from lab_wizard.lib.procedures.definition import ParamDecl, ParamTree, ProcedureDefinition, _SWEEP_ADAPTER
from lab_wizard.lib.procedures.spec import RenderContext, python_identifier


__all__ = [
    "class_prefix",
    "measurement_module_source",
    "procedure_block",
    "setup_template_source",
]

PROCEDURE_BLOCK = "procedure"
_SWEEP_MODULE = "lab_wizard.lib.measurements.general.sweep_params"


def class_prefix(name: str) -> str:
    """``mcr_curve`` -> ``McrCurve``."""
    return "".join(part.capitalize() for part in python_identifier(name).split("_") if part) or "Procedure"


def _imports(pairs: set[tuple[str, str]]) -> str:
    by_module: dict[str, set[str]] = {}
    for module, name in pairs:
        by_module.setdefault(module, set()).add(name)
    return "\n".join(
        f"from {module} import {', '.join(sorted(names))}" for module, names in sorted(by_module.items())
    )


# --------------------------- params models ---------------------------


def _field_line(name: str, decl: ParamDecl, imports: set[tuple[str, str]]) -> str:
    description = decl.full_description()
    desc = f", description={description!r}" if description else ""
    if decl.type == "sweep":
        # SweepParams is an Annotated alias, whose __module__ says "typing".
        imports.add((_SWEEP_MODULE, "SweepParams"))
        concrete = _SWEEP_ADAPTER.validate_python(decl.default)
        imports.add((type(concrete).__module__, type(concrete).__name__))
        args = ", ".join(f"{k}={v!r}" for k, v in concrete.model_dump().items() if k != "mode")
        return (
            f"    {name}: SweepParams = Field(default_factory=lambda: "
            f"{type(concrete).__name__}({args}){desc})"
        )
    return f"    {name}: {decl.type} = Field(default={decl.default!r}{desc})"


def params_models_source(tree: ParamTree, root_class: str, imports: set[tuple[str, str]]) -> str:
    """Pydantic classes for a param tree, innermost first, ``root_class`` last."""
    blocks: list[str] = []

    def emit(group: ParamTree, class_name: str, doc: str) -> None:
        lines: list[str] = [f"class {class_name}(BaseModel):", f"    {doc!r}"]
        if not group.entries:
            lines.append("    pass")
        for name, entry in group.entries.items():
            if isinstance(entry, ParamTree):
                sub = f"{class_name[: -len('Params')]}{class_prefix(name)}Params"
                emit(entry, sub, f"The {name!r} params group.")
                lines.append(f"    {name}: {sub} = Field(default_factory={sub})")
            else:
                lines.append(_field_line(name, entry, imports))
        blocks.append("\n".join(lines))

    imports.add(("pydantic", "BaseModel"))
    imports.add(("pydantic", "Field"))
    emit(tree, root_class, "Params for this procedure; validated from measurement.params in the project YAML.")
    return "\n\n\n".join(blocks)


# --------------------------- measurement module ---------------------------


def procedure_block(definition: ProcedureDefinition) -> tuple[str, set[tuple[str, str]]]:
    """The ``return <step tree>`` statement between the procedure markers."""
    definition.check()
    expr, ctx = definition.render_body()
    return f"return {expr}", ctx.imports


def measurement_module_source(definition: ProcedureDefinition) -> str:
    prefix = class_prefix(definition.name)
    fn = f"build_{python_identifier(definition.name)}_procedure"
    block, step_imports = procedure_block(definition)
    imports = set(step_imports) | {
        ("lab_procedure", "Point"),
        ("lab_procedure", "ProcedureRunner"),
        ("lab_procedure", "RunEnded"),
        ("lab_procedure", "RunStarted"),
        ("lab_procedure", "Status"),
        ("lab_procedure", "Step"),
        ("lab_wizard.lib.task_adapters", "PlotterSink"),
        ("lab_wizard.lib.task_adapters", "SaverSink"),
        ("lab_wizard.lib.task_adapters.provenance", "baseline_snapshot"),
    }
    roles = "\n".join(f"    {python_identifier(r)} = resources.{r}" for r in definition.roles)
    description = definition.description or f"The {definition.name} procedure."
    return f'''"""{description}

Generated by lab_wizard from the procedure ``{definition.name}``. The step tree
between the ``wizard:{PROCEDURE_BLOCK}`` markers is regenerated from that
definition; edit anything outside them freely.
"""

from __future__ import annotations

from typing import Any

{_imports(imports)}


def {fn}(resources: Any) -> Step:
    """Build the step tree for one run of ``{definition.name}``."""
    params = resources.params
{roles}
    # wizard:{PROCEDURE_BLOCK}:start
    {block}
    # wizard:{PROCEDURE_BLOCK}:end


class {prefix}Measurement:
    """Build, wire, and run the ``{definition.name}`` procedure for a set of resources."""

    def __init__(self, resources: Any) -> None:
        self.resources = resources

    def build_procedure(self) -> Step:
        return {fn}(self.resources)

    def run_measurement(self) -> Status:
        runner = ProcedureRunner(instruments=self.resources)
        bus = runner.context.data_bus
        message_types = (RunStarted, Point, RunEnded)
        bus.subscribe(message_types, SaverSink(self.resources.savers).handle)
        bus.subscribe(message_types, PlotterSink(self.resources.plotters).handle)
        run_started = RunStarted(
            run_type={definition.name!r},
            config=self.resources.params.model_dump(mode="json"),
            instruments=baseline_snapshot(self.resources),
        )
        return runner.run(self.build_procedure(), run_started)
'''


# --------------------------- setup template ---------------------------


def setup_template_source(definition: ProcedureDefinition) -> str:
    """A setup template with ``wizard:`` blocks, for the project generator to fill."""
    prefix = class_prefix(definition.name)
    behaviors = definition.role_behaviors()
    imports: set[tuple[str, str]] = {
        (cls.__module__, cls.__name__) for cls in behaviors.values()
    }
    params_source = params_models_source(definition.param_tree, f"{prefix}Params", imports)
    template_imports = _imports(imports)
    role_fields = "\n".join(f"    {role}: {cls.__name__}" for role, cls in behaviors.items())

    return f'''"""
Setup for the ``{definition.name}`` procedure, generated by lab_wizard.

The project generator fills the ``wizard:<block>`` regions with the selected
instruments, savers and plotters.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from lab_procedure import Status

from lab_wizard.lib.client.claims import RoutedClaims
from lab_wizard.lib.client.project_resources import local_claims_for, resource_source_for
from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver
from lab_wizard.lib.task_adapters.lifecycle import RunLifecycle
from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config
{template_imports}

# wizard:imports:start
# wizard inserts concrete instrument / saver / plotter imports here
# wizard:imports:end


{params_source}


@dataclass
class {prefix}Resources:
    # wizard:resource_fields:start
{role_fields}
    savers: list[GenericSaver] = field(default_factory=list)
    plotters: list[GenericPlotter] = field(default_factory=list)
    # wizard:resource_fields:end
    params: {prefix}Params = field(default_factory={prefix}Params)


def create_instrument_resources(
    project: ProjectConfig,
    resource_source: object | None = None,
) -> {prefix}Resources:
    resources = resource_source or project.resources
    # wizard:instantiation:start
    # wizard inserts config-backed instrument / saver / plotter construction here
    # wizard:instantiation:end

    return {prefix}Resources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
        # wizard:return_fields:end
        params={prefix}Params.model_validate(project.measurement.params),
    )


if __name__ == "__main__":
    import argparse

    # The procedure module is generated beside this setup file.
    from {definition.name} import {prefix}Measurement

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
        execute=lambda resources: {prefix}Measurement(resources).run_measurement(),
    )
    raise SystemExit(0 if status is Status.SUCCESS else 1)
'''
