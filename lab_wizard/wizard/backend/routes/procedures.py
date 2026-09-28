"""Procedures: the library, the composer's checks, presets, and saved plots."""

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
)
from lab_wizard.wizard.backend.models import Env

logger = logging.getLogger("lab_wizard.wizard.backend.routes.procedures")
router = APIRouter()


# --------------------------- Procedures section ---------------------------


class _DefinitionBody(BaseModel):
    definition: dict[str, Any]


class _YamlBody(BaseModel):
    yaml: str


class _PresetBody(BaseModel):
    values: dict[str, Any]


@router.get("/api/procedures")
def api_procedures(env: Env = Depends(get_env)):
    """Every procedure — built in and this workspace's — with whether it checks."""
    from lab_wizard.wizard.backend.procedures_api import procedure_summaries

    return {"procedures": procedure_summaries(workspace_config_dir(env))}


@router.get("/api/procedures/catalog")
def api_procedure_catalog(env: Env = Depends(get_env)):
    """Step types, behaviors, and which of this workspace's instruments fill each."""
    from lab_wizard.wizard.backend.procedures_api import composer_catalog

    return composer_catalog(workspace_config_dir(env))


@router.post("/api/procedures/check")
def api_procedure_check(body: _DefinitionBody):
    """Problems with a definition being edited, each with its path, and its Python."""
    from lab_wizard.wizard.backend.procedures_api import check_definition

    return check_definition(body.definition)


@router.post("/api/procedures/to-yaml")
def api_procedure_to_yaml(body: _DefinitionBody):
    from lab_wizard.wizard.backend.procedures_api import definition_to_yaml

    return {"yaml": definition_to_yaml(body.definition)}


@router.post("/api/procedures/from-yaml")
def api_procedure_from_yaml(body: _YamlBody):
    from lab_wizard.wizard.backend.procedures_api import definition_from_yaml

    try:
        return {"definition": definition_from_yaml(body.yaml)}
    except Exception as e:  # noqa: BLE001 - any YAML error is the user's to fix
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/procedures/{name}")
def api_procedure_detail(name: str, env: Env = Depends(get_env)):
    from lab_wizard.wizard.backend.procedures_api import procedure_detail

    try:
        return procedure_detail(workspace_config_dir(env), name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No procedure named {name!r}")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.put("/api/procedures/{name}")
def api_procedure_save(name: str, body: _DefinitionBody, env: Env = Depends(get_env)):
    """Save to this workspace. A built-in's name saves a workspace override of it."""
    from lab_wizard.lib.procedures.spec import ProcedureError
    from lab_wizard.wizard.backend.procedures_api import save_workspace_procedure

    if body.definition.get("name") != name:
        raise HTTPException(
            status_code=400,
            detail=f"The definition is named {body.definition.get('name')!r}, not {name!r}",
        )
    try:
        return save_workspace_procedure(workspace_config_dir(env), body.definition)
    except ProcedureError as e:
        raise HTTPException(status_code=422, detail="; ".join(e.problems))


@router.delete("/api/procedures/{name}")
def api_procedure_delete(name: str, env: Env = Depends(get_env)):
    """Delete this workspace's copy. Deleting an override brings the built-in back."""
    from lab_wizard.wizard.backend.procedures_api import delete_workspace_procedure

    try:
        return delete_workspace_procedure(workspace_config_dir(env), name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"This workspace has no procedure named {name!r}")


@router.get("/api/procedures/{name}/presets")
def api_procedure_presets(name: str, env: Env = Depends(get_env)):
    from lab_wizard.wizard.backend.procedures_api import procedure_presets

    try:
        return procedure_presets(workspace_config_dir(env), name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.put("/api/procedures/{name}/presets/{preset}")
def api_procedure_preset_save(name: str, preset: str, body: _PresetBody, env: Env = Depends(get_env)):
    from lab_wizard.wizard.backend.procedures_api import save_procedure_preset

    try:
        return save_procedure_preset(workspace_config_dir(env), name, preset, body.values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.delete("/api/procedures/{name}/presets/{preset}")
def api_procedure_preset_delete(name: str, preset: str, env: Env = Depends(get_env)):
    from lab_wizard.wizard.backend.procedures_api import delete_procedure_preset

    if not delete_procedure_preset(workspace_config_dir(env), name, preset):
        raise HTTPException(status_code=404, detail=f"No preset {preset!r} for {name!r}")
    return {"deleted": preset}


class _SavePlotBody(BaseModel):
    plot: dict[str, Any]
    # The plot to replace; by default, the one with the saved plot's own name.
    replace: str | None = None


@router.post("/api/procedures/{name}/plots")
def api_procedure_save_plot(name: str, body: _SavePlotBody, env: Env = Depends(get_env)):
    """Save a plot built on the Data page into its procedure."""
    try:
        return data_api.save_plot_to_procedure(workspace_config_dir(env), name, body.plot, replace=body.replace)
    except data_api.DataRequestError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
