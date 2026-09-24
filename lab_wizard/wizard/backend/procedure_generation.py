"""Generate and refresh projects from composed procedures.

A composed procedure (``config/procedures/<name>.yml``) reaches a project
through the same generator as a hand-written measurement: this module only
supplies the pieces a measurement directory would — a setup template, a
measurement module, requirements, and params — derived from the definition
instead of read from ``lib/measurements``. See ``plans/procedure_plan.md``
Phase 3.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.procedures.codegen import (
    DEFINITION_BLOCK,
    PROCEDURE_BLOCK,
    definition_block,
    measurement_module_source,
    procedure_block,
    setup_template_source,
)
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import load_procedure
from lab_wizard.lib.savers.saver import GenericSaver
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.wizard.backend.models import FilledReq
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    _params_for,
    _replace_wizard_block,
    generate_project,
)
from lab_wizard.wizard.backend.python_formatting import format_python_code


logger = logging.getLogger("lab_wizard.wizard.backend.procedure_generation")

__all__ = [
    "generate_procedure_project",
    "procedure_requirements",
    "refresh_procedure_source",
]


def procedure_requirements(definition: ProcedureDefinition) -> list[FilledReq]:
    """One instrument requirement per role, plus the savers and plotters every project takes."""
    reqs = [
        FilledReq(variable_name=role, base_type=behavior, resource_kind="instrument")
        for role, behavior in definition.role_behaviors().items()
    ]
    reqs.append(FilledReq(variable_name="savers", base_type=GenericSaver, resource_kind="saver", is_list=True))
    reqs.append(
        FilledReq(variable_name="plotters", base_type=GenericPlotter, resource_kind="plotter", is_list=True)
    )
    return reqs


def generate_procedure_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateProjectRequest,
) -> dict[str, Any]:
    """Generate a project for the procedure named ``req.measurement_name``."""
    definition = load_procedure(config_dir, req.measurement_name)
    logger.info("Generating project for procedure '%s'", definition.name)
    params = _params_for(config_dir, definition.name, req.params_preset, definition.params_model())
    return generate_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
        requirements=procedure_requirements(definition),
        template_text=setup_template_source(definition),
        measurement_source=format_python_code(measurement_module_source(definition)),
        params=params if params is not None else definition.param_defaults(),
    )


_IMPORT_LINE = re.compile(r"^from (\S+) import (.+)$")


def refresh_procedure_source(config_dir: Path, project_dir: Path) -> Path:
    """Regenerate a project's step tree from its procedure's current definition.

    Only the ``wizard:procedure`` block, and the ``wizard:definition`` block
    recording the definition it came from, are replaced, so anything edited
    outside them — an extra helper, a changed ``run_measurement`` — survives. Any step
    class the new tree needs and the file does not yet import is added.
    """
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    definition = load_procedure(config_dir, project.measurement_type)
    module_path = project_dir / f"{definition.name}.py"
    text = module_path.read_text(encoding="utf-8")

    block, needed = procedure_block(definition)
    text = _replace_wizard_block(text, PROCEDURE_BLOCK, block)
    text = _replace_wizard_block(text, DEFINITION_BLOCK, definition_block(definition))

    present: set[tuple[str, str]] = set()
    for line in text.splitlines():
        match = _IMPORT_LINE.match(line.strip())
        if match:
            for name in match.group(2).strip("()").split(","):
                if name.strip():
                    present.add((match.group(1), name.strip()))
    missing = sorted(needed - present)
    if missing:
        additions = "\n".join(f"from {module} import {name}" for module, name in missing)
        anchor = "from __future__ import annotations\n"
        text = text.replace(anchor, f"{anchor}\n{additions}\n", 1) if anchor in text else f"{additions}\n{text}"

    module_path.write_text(format_python_code(text), encoding="utf-8")
    logger.info("Refreshed the procedure block in %s", module_path)
    return module_path
