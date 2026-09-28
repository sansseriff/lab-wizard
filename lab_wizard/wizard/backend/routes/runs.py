"""The Run page: a project's settings, and starting, following and stopping it."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from lab_wizard.wizard.backend import launcher, project_config
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
    workspace_projects_dir,
)
from lab_wizard.wizard.backend.models import Env

logger = logging.getLogger("lab_wizard.wizard.backend.routes.runs")
router = APIRouter()


def _project_dir(env: Env, name: str) -> Path:
    try:
        return project_config.project_dir_for(workspace_projects_dir(env), name)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]))


@router.get("/api/projects/{name}/settings")
def api_project_settings(name: str, env: Env = Depends(get_env)):
    """The project's run details, params and outputs, as fields and as its YAML."""
    _project_dir(env, name)
    return project_config.read_project(workspace_projects_dir(env), Path(workspace_config_dir(env)), name)


@router.put("/api/projects/{name}/settings")
def api_project_settings_save(name: str, body: dict[str, Any], env: Env = Depends(get_env)):
    """Save ``{"yaml"}``, or any of ``{"run", "params", "outputs"}``; 422 lists every problem."""
    _project_dir(env, name)
    try:
        return project_config.save_project(workspace_projects_dir(env), Path(workspace_config_dir(env)), name, body)
    except project_config.ProjectConfigError as e:
        raise HTTPException(status_code=422, detail={"message": str(e), "problems": e.problems})


@router.get("/api/projects/{name}/launch")
def api_project_launch_status(name: str, env: Env = Depends(get_env)):
    return launcher.launch_status(_project_dir(env, name))


@router.post("/api/projects/{name}/launch")
def api_project_launch(name: str, env: Env = Depends(get_env)):
    """Run the project's setup file, as its own process."""
    try:
        return launcher.launch(_project_dir(env, name))
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post("/api/projects/{name}/stop")
def api_project_stop(name: str, env: Env = Depends(get_env)):
    """Ctrl-C the run: it aborts and puts its instruments in their safe state."""
    try:
        return launcher.stop(_project_dir(env, name))
    except launcher.LaunchError as e:
        raise HTTPException(status_code=409, detail=str(e))
