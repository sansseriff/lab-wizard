"""Generate projects from composed procedures, and update a project's procedure.

A composed procedure (``config/procedures/<name>.yml``) reaches a project
through the same generator as a custom measurement. The project carries a copy
of the definition in the ``procedure:`` block of its YAML; its measurement
module is built from that block (``lab_wizard.lib.project``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import load_procedure
from lab_wizard.lib.project import build_measurement_module
from lab_wizard.wizard.backend.models import FilledReq
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    _params_for,
    generate_project,
)

logger = logging.getLogger("lab_wizard.wizard.backend.procedure_generation")

__all__ = [
    "generate_procedure_project",
    "procedure_requirements",
    "update_project_procedure",
]


def procedure_requirements(definition: ProcedureDefinition) -> list[FilledReq]:
    """One instrument requirement per role."""
    return [
        FilledReq(variable_name=role, base_type=behavior)
        for role, behavior in definition.role_behaviors().items()
    ]


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
        params=params if params is not None else definition.param_defaults(),
        params_model=definition.params_model(),
        measurement=definition,
    )


def update_project_procedure(config_dir: Path, project_dir: Path) -> Path:
    """Replace a project's ``procedure:`` block with its procedure as the workspace has it now.

    A project keeps the procedure it was generated with until asked; this is
    the asking. Everything else in its YAML, and the comments in it, stay; its
    measurement module is built again from the new block.
    """
    yaml_path = project_dir / f"{project_dir.name}.yaml"
    rt = YAML(typ="rt")
    rt.default_flow_style = False
    document = rt.load(yaml_path.read_text(encoding="utf-8"))
    definition = load_procedure(config_dir, document["project"]["measurement_type"])
    document["procedure"] = definition.model_dump(mode="json", exclude_none=True)
    with yaml_path.open("w", encoding="utf-8") as handle:
        rt.dump(document, handle)
    build_measurement_module(project_dir, force=True)
    logger.info("Updated the procedure of %s from %s", project_dir.name, definition.name)
    return yaml_path
