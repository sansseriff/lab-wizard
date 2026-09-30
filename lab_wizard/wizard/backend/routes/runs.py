"""The Run page: a project's settings, and starting, following and stopping it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lab_wizard.lib.utilities.model_tree import OutputsConfig, RunConfig

from lab_wizard.wizard.backend import data_api, launcher, project_config
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
    params: dict[str, Any] | None = None
    outputs: dict[str, Any] | None = None


@router.get("/api/projects/{name}/settings", response_model=ProjectSettings)
def api_project_settings(name: str, env: Env = Depends(get_env)):
    """The project's run details, params and outputs, as fields and as its YAML."""
    _project_dir(env, name)
    return project_config.read_project(workspace_projects_dir(env), Path(workspace_config_dir(env)), name)


@router.put("/api/projects/{name}/settings", response_model=ProjectSettings)
def api_project_settings_save(name: str, body: SettingsBody, env: Env = Depends(get_env)):
    """Save ``{"yaml"}``, or any of ``{"run", "params", "outputs"}``; 422 lists every problem."""
    _project_dir(env, name)
    try:
        return project_config.save_project(
            workspace_projects_dir(env),
            Path(workspace_config_dir(env)),
            name,
            body.model_dump(exclude_none=True),
            known_devices={d["name"] for d in data_api.device_list(workspace_database(env))},
        )
    except project_config.ProjectConfigError as e:
        raise RequestProblem(422, str(e), e.problems)


@router.get("/api/projects/{name}/launch", response_model=LaunchStatus)
def api_project_launch_status(name: str, env: Env = Depends(get_env)):
    return launcher.launch_status(_project_dir(env, name))


@router.post("/api/projects/{name}/launch", response_model=LaunchStatus)
def api_project_launch(name: str, env: Env = Depends(get_env)):
    """Run the project's setup file, as its own process."""
    try:
        return launcher.launch(_project_dir(env, name))
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/projects/{name}/stop", response_model=LaunchStatus)
def api_project_stop(name: str, env: Env = Depends(get_env)):
    """Ctrl-C the run: it aborts and puts its instruments in their safe state."""
    try:
        return launcher.stop(_project_dir(env, name))
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))
