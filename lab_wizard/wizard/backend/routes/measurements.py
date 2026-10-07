"""Measurements: what can be run, what it needs, and the projects generated from it."""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from lab_wizard.lib.custom_measurements import list_custom_measurements
from lab_wizard.wizard.backend.custom_measurement_generation import (
    custom_measurement,
    custom_requirements,
    generate_custom_measurement_project,
)
from lab_wizard.wizard.backend.custom_resource_generation import (
    GenerateCustomResourceRequest,
    generate_custom_resource_project,
)
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
    workspace_projects_dir,
)
from lab_wizard.wizard.backend.get_measurements import discover_matching_instruments
from lab_wizard.wizard.backend.models import (
    Env,
    OutputReq,
    RemoteMatch,
)
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest
from lab_wizard.wizard.backend.projects import list_projects
from lab_wizard.wizard.backend.transport_status import servers_holding
from lab_wizard.wizard.backend.remote_servers import (
    list_remote_attributes,
)
from lab_wizard.lib.procedures.storage import (
    list_presets,
    list_procedures,
    load_procedure,
    procedure_origin,
)
from lab_wizard.wizard.backend.procedure_generation import (
    procedure_requirements,
    generate_procedure_project,
)

logger = logging.getLogger("lab_wizard.wizard.backend.routes.measurements")
router = APIRouter()


@router.get("/api/measurement-choices")
def api_measurement_choices(env: Env = Depends(get_env)):
    """Everything a measurement can be created from, in one list.

    Procedures — this workspace's own, and the ones built into lab_wizard — and
    the workspace's custom measurements (Python files in its measurements
    folder) are offered side by side, because to the person creating a
    measurement they are the same kind of thing: roles to bind, params to set.
    A file that cannot be loaded is listed with why, not hidden.
    """

    config_dir = workspace_config_dir(env)
    choices: list[dict] = []
    for name, found in list_custom_measurements(env.measurements_dir).items():
        if isinstance(found, str):
            choices.append({"name": name, "kind": "custom", "origin": "workspace",
                            "description": "", "error": found, "presets": []})
            continue
        choices.append(
            {
                "name": name,
                "kind": "custom",
                "origin": "workspace",
                "description": found.description,
                "roles": {role: behavior.__name__ for role, (behavior, _is_list) in found.roles.items()},
                "presets": list_presets(config_dir, name),
            }
        )
    for name in list_procedures(config_dir):
        try:
            definition = load_procedure(config_dir, name)
        except Exception as e:  # noqa: BLE001 - a broken definition is listed, not hidden
            choices.append({"name": name, "kind": "procedure", "origin": procedure_origin(config_dir, name),
                            "description": "", "error": str(e), "presets": []})
            continue
        choices.append(
            {
                "name": name,
                "kind": "procedure",
                "origin": procedure_origin(config_dir, name),
                "description": definition.description,
                "roles": {role: decl.behavior for role, decl in definition.roles.items()},
                "records": definition.emitted_fields(),
                # What its derived columns read from the setup, for the
                # measurement to bind to the setup's fields.
                "needs": {need: decl.model_dump(mode="json") for need, decl in definition.needs.items()},
                "presets": list_presets(config_dir, name),
            }
        )
    return {"choices": choices}


def _requirements_for(name: str, kind: str, env: Env):
    """``FilledReq``s for a procedure or a custom measurement, or a 404."""
    if kind == "procedure":
        try:
            return procedure_requirements(load_procedure(workspace_config_dir(env), name))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    try:
        return custom_requirements(custom_measurement(env.measurements_dir, name))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/get-resources/{name}")
def get_resources(
    name: str,
    kind: str = "procedure",
    env: Env = Depends(get_env),
    verbose: bool = False,
):
    """Return the instrument roles a measurement needs, each with what could fill it.

    ``kind`` is ``procedure`` for a procedure definition or ``custom`` for a
    custom measurement; both answer the same shape.
    ``matching_instruments`` is populated by class-hierarchy discovery and
    ``matching_remote`` from registered servers.
    """
    logger.info("Getting resources for %s '%s'", kind, name)
    reqs = _requirements_for(name, kind, env)
    config_dir = workspace_config_dir(env)

    # Enumerate remote attributes once (best-effort; unreachable servers are
    # skipped) so each instrument requirement can offer remote matches.
    try:
        remote_attrs = list_remote_attributes(config_dir)
    except Exception as e:
        logger.warning("Could not enumerate remote attributes: %s", e)
        remote_attrs = []

    outputs: list[OutputReq] = []
    for req in reqs:
        try:
            matches = discover_matching_instruments(env, req.base_type)
        except Exception as e:
            logger.exception(
                "Discovery error for requirement '%s': %s", req.variable_name, e
            )
            matches = []
        base_name = getattr(req.base_type, "__name__", str(req.base_type))
        remote_matches = [
            RemoteMatch(**attr)
            for attr in remote_attrs
            if attr.get("behavior_abc") == base_name and attr.get("attribute")
        ]
        outputs.append(
            OutputReq(
                variable_name=req.variable_name,
                base_type=str(req.base_type),
                is_list=req.is_list,
                matching_instruments=matches,
                matching_remote=remote_matches,
            )
        )

    if verbose:
        logger.debug("Final resource requirements for '%s': %s", name, outputs)
    return outputs


@router.get("/api/projects")
def api_list_projects(env: Env = Depends(get_env)):
    """Projects this workspace has already generated, newest first.

    Read back off disk from the YAML generation already writes, so this records
    no new state and cannot disagree with what is actually there.
    """
    return {"projects": list_projects(workspace_projects_dir(env))}


@router.post("/api/create-measurement-project")
def api_create_measurement_project(
    body: GenerateProjectRequest,
    env: Env = Depends(get_env),
):
    """Create a project from a procedure or a custom measurement (``body.kind``)."""
    config_dir = Path(workspace_config_dir(env))
    try:
        if body.kind == "procedure":
            result = generate_procedure_project(
                config_dir=config_dir,
                projects_dir=workspace_projects_dir(env),
                req=body,
            )
        else:
            result = generate_custom_measurement_project(
                config_dir=config_dir,
                projects_dir=workspace_projects_dir(env),
                measurements_dir=env.measurements_dir,
                req=body,
            )
    except Exception as e:
        logger.exception("Create measurement project API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    return _with_held_hardware(config_dir, result)


@router.post("/api/create-custom-resource-project")
def api_create_custom_resource_project(
    body: GenerateCustomResourceRequest,
    env: Env = Depends(get_env),
):
    """Create a new timestamped project containing a programmatically built setup file."""
    config_dir = Path(workspace_config_dir(env))
    try:
        result = generate_custom_resource_project(
            config_dir=config_dir,
            projects_dir=workspace_projects_dir(env),
            req=body,
        )
    except Exception as e:
        logger.exception("Create custom resource project API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    return _with_held_hardware(config_dir, result)


def _with_held_hardware(config_dir: Path, result: dict) -> dict:
    """Add the servers that hold hardware the generated file opens itself.

    The project is written either way; this is what the person needs to know
    before running it. Never fails the request: the files already exist.
    """
    try:
        held = servers_holding(config_dir, result.get("local_roots", []))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not ask servers what they hold: %s", e)
        held = []
    return {**result, "held_by_servers": held}


# Serve SvelteKit static build from resolved directory at root (mounted last)
