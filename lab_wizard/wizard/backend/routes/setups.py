"""The Setups page: each experiment's current facts, and its pictures.

A setup lives in the lab database (``lib/data/setups.py``); projects name it
in their ``setup:`` block, and each run copies its fields when it starts.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from lab_wizard.lib.data.schema import open_database
from lab_wizard.lib.data.setups import (
    IMAGES_DIR,
    SetupError,
    delete_setup,
    list_setups,
    save_image,
    save_setup,
)
from lab_wizard.wizard.backend.deps import get_env, workspace_database, workspace_projects_dir
from lab_wizard.wizard.backend.models import Env, ResponseModel

logger = logging.getLogger("lab_wizard.wizard.backend.routes.setups")
router = APIRouter()


class Setup(ResponseModel):
    name: str
    notes: str | None
    # Free-form: groups, and leaves that are text, numbers, true/false,
    # quantities ({value, unit}) or pictures ({image}).
    fields: dict[str, Any]
    device: str | None
    # How many runs were taken on it, and when the last one started.
    runs: int
    last_run: str | None
    # The projects whose setup: block names it.
    projects: list[str]


class SetupBody(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict)
    device: str | None = None
    notes: str | None = None


class ImageSaved(ResponseModel):
    image: str


@contextmanager
def _lab(env: Env) -> Iterator[sqlite3.Connection]:
    connection = open_database(workspace_database(env))
    try:
        yield connection
    finally:
        connection.close()


def _projects_by_setup(projects_dir: Path) -> dict[str, list[str]]:
    """``{setup: [project]}``, read off each project's YAML."""
    out: dict[str, list[str]] = {}
    for path in sorted(projects_dir.glob("*/*.yaml")):
        if path.stem != path.parent.name:
            continue
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        name = (data.get("setup") or {}).get("name") if isinstance(data, dict) else None
        if isinstance(name, str) and name:
            out.setdefault(name, []).append(path.parent.name)
    return out


def _with_projects(setups: list[dict[str, Any]], env: Env) -> list[dict[str, Any]]:
    projects = _projects_by_setup(workspace_projects_dir(env))
    return [{**s, "projects": projects.get(s["name"], [])} for s in setups]


def _one(connection: sqlite3.Connection, env: Env, name: str) -> dict[str, Any]:
    found = next((s for s in list_setups(connection) if s["name"] == name), None)
    if found is None:
        raise HTTPException(status_code=404, detail=f"No setup named {name!r}")
    (out,) = _with_projects([found], env)
    return out


@router.get("/api/setups", response_model=list[Setup])
def api_setups(env: Env = Depends(get_env)):
    """Every setup, with its current fields and mounted device."""
    with _lab(env) as connection:
        return _with_projects(list_setups(connection), env)


@router.get("/api/setups/{name}", response_model=Setup)
def api_setup(name: str, env: Env = Depends(get_env)):
    with _lab(env) as connection:
        return _one(connection, env, name)


@router.put("/api/setups/{name}", response_model=Setup)
def api_setup_save(name: str, body: SetupBody, env: Env = Depends(get_env)):
    """Create a setup, or replace its fields, device and notes. Past runs keep their copies."""
    with _lab(env) as connection:
        try:
            saved = save_setup(connection, name, body.fields, device=body.device, notes=body.notes)
        except SetupError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return _one(connection, env, saved["name"])


@router.delete("/api/setups/{name}")
def api_setup_delete(name: str, env: Env = Depends(get_env)):
    """Forget a setup. Its runs keep their copies; projects naming it cannot run until they name another."""
    with _lab(env) as connection:
        try:
            delete_setup(connection, name)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e.args[0]))
    return {"deleted": name}


@router.put("/api/setup-images", response_model=ImageSaved)
async def api_setup_image_save(request: Request, suffix: str, env: Env = Depends(get_env)):
    """Keep a picture sent as the request body; returns what a field refers to it by."""
    if env.data_dir is None:
        raise HTTPException(status_code=500, detail="Workspace data directory was not resolved")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=422, detail="The picture is empty")
    try:
        return {"image": save_image(env.data_dir, content, suffix)}
    except SetupError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/api/setup-images/{image}")
def api_setup_image(image: str, env: Env = Depends(get_env)):
    if env.data_dir is None or "/" in image or "\\" in image or image.startswith("."):
        raise HTTPException(status_code=404, detail="No such picture")
    path = env.data_dir / IMAGES_DIR / image
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No such picture")
    # Named by what is in it, so it never changes.
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})
