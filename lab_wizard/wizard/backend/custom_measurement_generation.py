"""Generate projects from custom measurements.

A custom measurement (``<workspace>/measurements/<name>.py``, see
``lab_wizard.lib.custom_measurements``) reaches a project through the same
generator as a procedure: this module supplies the pieces — the file itself,
a setup template importing its ``Resources`` and ``Params``, its roles as
requirements, and its default params.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from lab_wizard.lib.custom_measurements import (
    CustomMeasurement,
    list_custom_measurements,
)
from lab_wizard.wizard.backend.models import FilledReq
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    _params_for,
    generate_project,
)

logger = logging.getLogger("lab_wizard.wizard.backend.custom_measurement_generation")

__all__ = ["custom_measurement", "custom_requirements", "generate_custom_measurement_project"]


def custom_measurement(measurements_dir: Path | None, name: str) -> CustomMeasurement:
    """The custom measurement called ``name``; ``ValueError`` if it is missing or broken."""
    found = list_custom_measurements(measurements_dir).get(name)
    if found is None:
        raise ValueError(f"No custom measurement named {name!r} in {measurements_dir}")
    if isinstance(found, str):
        raise ValueError(f"{name}.py cannot be used: {found}")
    return found


def custom_requirements(measurement: CustomMeasurement) -> list[FilledReq]:
    """One instrument requirement per role its ``Resources`` declares."""
    return [
        FilledReq(variable_name=role, base_type=behavior, is_list=is_list)
        for role, (behavior, is_list) in measurement.roles.items()
    ]


def generate_custom_measurement_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    measurements_dir: Path | None,
    req: GenerateProjectRequest,
) -> dict[str, Any]:
    """Generate a project for the custom measurement named ``req.measurement_name``."""
    measurement = custom_measurement(measurements_dir, req.measurement_name)
    logger.info("Generating project for custom measurement '%s'", measurement.name)
    params = _params_for(config_dir, measurement.name, req.params_preset, measurement.params_model)
    return generate_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
        requirements=custom_requirements(measurement),
        params=params if params is not None else measurement.params_model().model_dump(mode="json"),
        params_model=measurement.params_model,
        measurement=measurement,
    )
