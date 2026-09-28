"""Workspace settings: what applies to every project at once."""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lab_wizard.lib.workspace import MANIFEST_NAME
from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.deps import (
    call_data_api,
    get_env,
    workspace_config_dir,
    workspace_database,
    workspace_root,
)
from lab_wizard.wizard.backend.models import Env

logger = logging.getLogger("lab_wizard.wizard.backend.routes.settings")
router = APIRouter()


@router.get("/api/settings/workspace")
def api_settings_workspace(env: Env = Depends(get_env)):
    """Where this workspace keeps each kind of thing, as the manifest resolves it."""
    root = workspace_root(env)
    return {
        "root": str(root),
        "manifest": str(root / MANIFEST_NAME),
        "config_dir": str(env.config_dir),
        "projects_dir": str(env.projects_dir),
        "logs_dir": str(env.logs_dir),
        "data_dir": str(env.data_dir),
        "database": str(workspace_database(env)),
    }


@router.get("/api/settings/files")
def api_settings_files(env: Env = Depends(get_env)):
    """How runs of projects with file saving on are laid out on disk."""
    return data_api.file_settings(workspace_config_dir(env), workspace_root(env), env.data_dir)


@router.put("/api/settings/files")
def api_settings_files_save(body: dict[str, Any], env: Env = Depends(get_env)):
    return call_data_api(data_api.save_file_settings, workspace_config_dir(env), workspace_root(env), env.data_dir, body)


class _TemplateBody(BaseModel):
    path: str


@router.post("/api/settings/files/check")
def api_settings_files_check(body: _TemplateBody, env: Env = Depends(get_env)):
    """What a folder template would name the latest run's folder, and what is wrong with it."""
    return data_api.check_file_template(env.data_dir, body.path)
