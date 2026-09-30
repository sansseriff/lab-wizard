"""Workspace settings: what applies to every project at once."""

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lab_wizard.lib.data.network import warn_if_networked
from lab_wizard.lib.data.settings import FileSettings
from lab_wizard.lib.workspace import MANIFEST_NAME
from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.deps import (
    call_data_api,
    get_env,
    workspace_config_dir,
    workspace_database,
    workspace_root,
)
from lab_wizard.wizard.backend.models import Env, ResponseModel

logger = logging.getLogger("lab_wizard.wizard.backend.routes.settings")
router = APIRouter()


class WorkspacePaths(ResponseModel):
    """Where this workspace keeps each kind of thing, as lab-wizard.toml resolves it."""

    root: str
    manifest: str
    config_dir: str
    projects_dir: str
    logs_dir: str
    data_dir: str
    database: str
    # Set when the database is on a network share, where SQLite is unsafe.
    database_warning: str | None


class TemplateProblem(ResponseModel):
    """Something wrong with a folder template: an error blocks saving, a warning does not."""

    level: Literal["error", "warning"]
    key: str
    message: str


class TemplateCheck(ResponseModel):
    # Where the latest run's folder would go (a made-up run before there is one).
    example: str
    problems: list[TemplateProblem]


class FileSettingsView(TemplateCheck):
    files: FileSettings
    # The folder runs go under, resolved.
    folder: str
    # Every key a template can use: the fixed ones, then those this lab has recorded.
    keys: list[str]


@router.get("/api/settings/workspace", response_model=WorkspacePaths)
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
        # Set when the database is on a network share, where SQLite is unsafe.
        "database_warning": warn_if_networked(workspace_database(env)),
    }


@router.get("/api/settings/files", response_model=FileSettingsView)
def api_settings_files(env: Env = Depends(get_env)):
    """How runs of projects with file saving on are laid out on disk."""
    return data_api.file_settings(workspace_config_dir(env), workspace_root(env), env.data_dir)


@router.put("/api/settings/files", response_model=FileSettingsView)
def api_settings_files_save(body: FileSettings, env: Env = Depends(get_env)):
    return call_data_api(
        data_api.save_file_settings, workspace_config_dir(env), workspace_root(env), env.data_dir, body.model_dump()
    )


class _TemplateBody(BaseModel):
    path: str


@router.post("/api/settings/files/check", response_model=TemplateCheck)
def api_settings_files_check(body: _TemplateBody, env: Env = Depends(get_env)):
    """What a folder template would name the latest run's folder, and what is wrong with it."""
    return data_api.check_file_template(env.data_dir, body.path)
