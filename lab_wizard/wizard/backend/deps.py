"""What every route needs: the workspace it serves, and where that workspace keeps things."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request

# Backend modules import what they use at module level, so everything is
# imported when the app is built and never inside a request: two requests that
# import one package at once from different modules can deadlock on its import
# locks, and pages send their first requests in parallel.
from lab_wizard.lib.data.schema import DATABASE_NAME
from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.models import Env

__all__ = [
    "call_data_api",
    "get_env",
    "workspace_config_dir",
    "workspace_database",
    "workspace_projects_dir",
    "workspace_root",
]


def get_env(request: Request) -> Env:
    """Dependency to provide process-wide Env stored on app.state."""
    env = getattr(request.app.state, "env", None)
    if env is None:
        # Fallback: create once if not present (e.g., during tests)
        env = Env.from_current_workspace()
        request.app.state.env = env
    return env


def workspace_config_dir(env: Env) -> str:
    if env.config_dir is None:
        raise RuntimeError("Workspace config directory was not resolved")
    return str(env.config_dir)


def workspace_projects_dir(env: Env) -> Path:
    if env.projects_dir is None:
        raise RuntimeError("Workspace projects directory was not resolved")
    return env.projects_dir


def workspace_root(env: Env) -> Path:
    if env.workspace_dir is None or env.data_dir is None:
        raise RuntimeError("Workspace directories were not resolved")
    return env.workspace_dir


def workspace_database(env: Env) -> Path:
    if env.data_dir is None:
        raise RuntimeError("Workspace data directory was not resolved")
    return env.data_dir / DATABASE_NAME


def call_data_api(call, *args: Any, **kwargs: Any) -> Any:
    """Run a ``data_api`` call, turning what it raises into HTTP errors."""
    try:
        return call(*args, **kwargs)
    except data_api.DataRequestError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="No runs have been recorded in this workspace yet")
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e.args[0]) if e.args else "Not found")
