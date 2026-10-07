"""The Run page: a project's settings, and starting, following and stopping it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lab_wizard.lib.data.schema import open_database
from lab_wizard.lib.data.setups import SetupError, list_setups
from lab_wizard.lib.procedures.definition import NeedDecl
from lab_wizard.lib.utilities.model_tree import OutputsConfig, RunConfig, SetupBinding

from lab_wizard.wizard.backend import launcher, project_config
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
    workspace_database,
    workspace_projects_dir,
)
from lab_wizard.wizard.backend.errors import RequestProblem
from lab_wizard.wizard.backend.models import Env, ResponseModel

logger = logging.getLogger("lab_wizard.wizard.backend.routes.runs")
router = APIRouter()


def _project_dir(env: Env, name: str) -> Path:
    try:
        return project_config.project_dir_for(workspace_projects_dir(env), name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]))


class ProjectSettings(ResponseModel):
    """A project's settings, as the Run page edits them: fields, and the YAML they come from."""

    name: str
    path: str
    measurement: str
    kind: Literal["procedure", "custom"]
    # ``embedded``: every setting is in the setup file, and this YAML is not read.
    style: Literal["production", "embedded"]
    setup_file: str | None
    yaml: str
    run: RunConfig
    setup: SetupBinding
    # The procedure's needs, for the setup's fields to be bound to.
    needs: dict[str, NeedDecl]
    params: dict[str, Any]
    outputs: OutputsConfig
    # The params' JSON schema, when the measurement's params model can be loaded.
    params_schema: dict[str, Any] | None


class LaunchStatus(ResponseModel):
    """Whether the project is running from here: ``idle``, ``starting``, ``running`` or ``ended``."""

    state: Literal["idle", "starting", "running", "ended"]
    launch_id: str | None = None
    pid: int | None = None
    started_at: str | None = None
    run_id: int | None = None
    exit_code: int | None = None
    log: str | None = None
    log_file: str | None = None


class SettingsBody(BaseModel):
    """The whole YAML, or any of the sections."""

    yaml: str | None = None
    run: dict[str, Any] | None = None
    setup: dict[str, Any] | None = None
    params: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None


@router.get("/api/projects/{name}/settings", response_model=ProjectSettings)
def api_project_settings(name: str, env: Env = Depends(get_env)):
    """The project's run details, params and outputs, as fields and as its YAML."""
    _project_dir(env, name)
    return project_config.read_project(workspace_projects_dir(env), Path(workspace_config_dir(env)), name)


@router.put("/api/projects/{name}/settings", response_model=ProjectSettings)
def api_project_settings_save(name: str, body: SettingsBody, env: Env = Depends(get_env)):
    """Save ``{"yaml"}``, or any of ``{"run", "setup", "params", "outputs"}``; 422 lists every problem."""
    _project_dir(env, name)
    connection = open_database(workspace_database(env))
    try:
        known_setups = {s["name"] for s in list_setups(connection)}
    finally:
        connection.close()
    try:
        return project_config.save_project(
            workspace_projects_dir(env),
            Path(workspace_config_dir(env)),
            name,
            body.model_dump(exclude_none=True),
            known_setups=known_setups,
        )
    except project_config.ProjectConfigError as e:
        raise RequestProblem(422, str(e), e.problems)


@router.get("/api/projects/{name}/launch", response_model=LaunchStatus)
def api_project_launch_status(name: str, env: Env = Depends(get_env)):
    return launcher.launch_status(_project_dir(env, name))


@router.post("/api/projects/{name}/launch", response_model=LaunchStatus)
def api_project_launch(name: str, env: Env = Depends(get_env)):
    """Run the project's setup file, as its own process.

    Refused (409) if its setup is missing or cannot fill the procedure's
    needs: the run would refuse to start anyway, after claiming instruments.
    """
    project_dir = _project_dir(env, name)
    try:
        project_config.check_setup(project_dir, workspace_database(env))
    except SetupError as e:
        raise HTTPException(status_code=409, detail=str(e))
    try:
        return launcher.launch(project_dir)
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/projects/{name}/stop", response_model=LaunchStatus)
def api_project_stop(name: str, env: Env = Depends(get_env)):
    """Ctrl-C the run: it aborts and puts its instruments in their safe state."""
    try:
        return launcher.stop(_project_dir(env, name))
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))
