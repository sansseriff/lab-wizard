"""The Data page: the lab database, read through ``data_api``."""

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.deps import (
    call_data_api,
    get_env,
    workspace_config_dir,
    workspace_database,
)
from lab_wizard.wizard.backend.models import Env

logger = logging.getLogger("lab_wizard.wizard.backend.routes.data")
router = APIRouter()


# --------------------------- data: the lab database ---------------------------


class _SpecBody(BaseModel):
    spec: dict[str, Any]


class _DeviceBody(BaseModel):
    properties: dict[str, Any] = Field(default_factory=dict)
    notes: str | None = None


@router.get("/api/data/facets")
def api_data_facets(filters: str | None = None, env: Env = Depends(get_env)):
    """Every filter, with how many runs each value leaves under ``filters`` (JSON)."""
    return call_data_api(lambda: data_api.facet_list(workspace_database(env), data_api.parse_filters(filters)))


@router.get("/api/data/runs")
def api_data_runs(filters: str | None = None, page: int = 1, page_size: int = 100, env: Env = Depends(get_env)):
    return call_data_api(
        lambda: data_api.run_list(workspace_database(env), data_api.parse_filters(filters), page=page, page_size=page_size)
    )


@router.get("/api/data/runs/{run_id}")
def api_data_run(run_id: int, env: Env = Depends(get_env)):
    return call_data_api(data_api.run_detail, workspace_database(env), workspace_config_dir(env), run_id)


@router.get("/api/data/runs/{run_id}/steps")
def api_data_run_steps(run_id: int, env: Env = Depends(get_env)):
    return {"steps": call_data_api(data_api.run_steps, workspace_database(env), run_id)}


@router.get("/api/data/runs/{run_id}/points/{seq}")
def api_data_point(run_id: int, seq: int, env: Env = Depends(get_env)):
    return call_data_api(data_api.point_detail, workspace_database(env), run_id, seq)


@router.get("/api/data/runs/{run_id}/export")
def api_data_export(run_id: int, env: Env = Depends(get_env)):
    """The run as a zipped folder of CSV and YAML, as the file saver writes it."""
    from fastapi.responses import Response

    content, filename = call_data_api(data_api.export_zip, workspace_database(env), run_id)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/data/plot")
def api_data_plot(body: _SpecBody, env: Env = Depends(get_env)):
    return call_data_api(data_api.plot, workspace_database(env), workspace_config_dir(env), body.spec)


@router.post("/api/data/plot/notebook")
def api_data_plot_notebook(body: _SpecBody, env: Env = Depends(get_env)):
    return call_data_api(data_api.notebook, workspace_database(env), workspace_config_dir(env), body.spec)


@router.get("/api/data/devices")
def api_data_devices(env: Env = Depends(get_env)):
    return {"devices": call_data_api(data_api.device_list, workspace_database(env))}


@router.put("/api/data/devices/{name}")
def api_data_device_save(name: str, body: _DeviceBody, env: Env = Depends(get_env)):
    """Create or update a device; its properties become filters on all its runs."""
    return call_data_api(data_api.save_device, workspace_database(env), name, body.properties, body.notes)
