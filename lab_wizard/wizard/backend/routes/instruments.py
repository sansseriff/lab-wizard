"""Instruments: this workspace's configured tree, drafts, discovery, and transports."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lab_wizard.lib.utilities.config_io import (
    add_instrument_chain,
    get_configured_tree,
    reinitialize_instrument,
    remove_instrument,
)
from lab_wizard.lib.utilities.resource_catalog import (
    get_instrument_metadata,
    load_params_class,
)
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
    workspace_projects_dir,
)
from lab_wizard.wizard.backend.hardware_access import (
    apply_tree_edit,
    run_discovery,
)
from lab_wizard.wizard.backend.instrument_drafts import drafts as _instrument_drafts
from lab_wizard.wizard.backend.instrument_sources import list_instrument_sources
from lab_wizard.wizard.backend.models import Env
from lab_wizard.wizard.backend.permissions_api import (
    attributes_under,
    rules_referencing,
)
from lab_wizard.wizard.backend.projects import projects_referencing
from lab_wizard.wizard.backend.transport_status import (
    conflicts_for_selection,
    duplicate_transport_check,
    transport_overview,
)
from lab_wizard.lib.utilities.config_io import (
    update_instrument_params,
    apply_discovered_children,
)
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.utilities.instrument_discovery import (
    discover_with_registry,
    draft_discovery_tree,
)

logger = logging.getLogger("lab_wizard.wizard.backend.routes.instruments")
router = APIRouter()


@router.get("/api/manage-instruments")
def api_manage_instruments(env: Env = Depends(get_env)):
    """Return the configured tree and metadata for all discoverable types."""
    config_dir = workspace_config_dir(env)
    tree = get_configured_tree(config_dir)
    metadata = get_instrument_metadata()
    return {"tree": tree, "metadata": metadata}


@router.get("/api/instrument-sources")
def api_instrument_sources(env: Env = Depends(get_env)):
    """Every place this workspace can get an instrument from.

    Local tree, other workspaces' daemons on this machine (full trees, editable),
    and registered remote servers (named leaves only — a remote peer gets read +
    call, never reconfiguration). Unreachable sources are reported rather than
    raised so one rack being off does not block authoring against the others.
    """
    return list_instrument_sources(workspace_config_dir(env))


@router.get("/api/transport-status")
def api_transport_status(env: Env = Depends(get_env)):
    """Per-root sharing/authority declarations plus what the server holds now.

    Lets the tree show whether a rack is exclusive or shared, and whether it is
    currently in use, without the user having to run anything to find out.
    """
    return transport_overview(workspace_config_dir(env))


class _ConflictCheckRequest(BaseModel):
    paths: list[str] = Field(default_factory=list)


@router.post("/api/transport-status/check")
def api_transport_conflicts(
    req: _ConflictCheckRequest, env: Env = Depends(get_env)
):
    """Would a *local* project using these instruments contend with the server?

    Used during measurement creation so a conflict is a design-time answer
    rather than a 2am failure.
    """
    return conflicts_for_selection(workspace_config_dir(env), req.paths)


class _DuplicateCheckRequest(BaseModel):
    type: str
    key: str = ""
    # Present when checking a local add; omitted when the target is a server.
    include_local: bool = True


@router.post("/api/transport-status/duplicate-check")
def api_duplicate_transport(
    req: _DuplicateCheckRequest, env: Env = Depends(get_env)
):
    """Would adding this instrument point a second config at one device?

    Asked before the write. Two workspaces naming one serial port is a mistake
    whose first symptom is otherwise a lease refusal in the middle of a run.
    """
    return duplicate_transport_check(
        req.type,
        req.key,
        config_dir=workspace_config_dir(env) if req.include_local else None,
    )




class _ChainStep(BaseModel):
    type: str
    key: str
    action: str  # "create_new" | "use_existing"
    extra: dict = Field(
        default_factory=dict
    )  # optional extra fields to set on newly-created params

    children: list[dict] = Field(default_factory=list)


class _AddBody(BaseModel):
    chain: list[_ChainStep]


class _ResetBody(BaseModel):
    type: str
    key: str
    path: list[dict[str, str]] | None = None


class _RemoveBody(BaseModel):
    type: str
    key: str
    path: list[dict[str, str]] | None = None


@router.post("/api/manage-instruments/add")
def api_add_instrument(body: _AddBody, env: Env = Depends(get_env)):
    """Add an instrument (with optional parent chain creation).

    Routed through this workspace's server when one is running, so a local edit
    gets the same held-rack refusal, registry reload and audit entry that an
    edit from another workspace already got.
    """
    config_dir = workspace_config_dir(env)
    try:
        chain_dicts = [s.model_dump() for s in body.chain]
        return apply_tree_edit(
            config_dir,
            "add",
            {"chain": chain_dicts},
            lambda: add_instrument_chain(config_dir, chain_dicts),
        )
    except Exception as e:
        logger.exception("Add instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


class _InstrumentPath(BaseModel):
    type: str
    key: str


class _UpdateInstrumentBody(BaseModel):
    path: list[_InstrumentPath]
    fields: dict
    expected_fields: dict


@router.post("/api/manage-instruments/update")
def api_update_instrument(body: _UpdateInstrumentBody, env: Env = Depends(get_env)):
    config_dir = workspace_config_dir(env)
    payload = body.model_dump()
    try:
        return apply_tree_edit(
            config_dir,
            "update",
            payload,
            lambda: update_instrument_params(config_dir, **payload),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/manage-instruments/reset")
def api_reset_instrument(body: _ResetBody, env: Env = Depends(get_env)):
    """Reset an instrument's config to factory defaults (preserves children)."""
    config_dir = workspace_config_dir(env)
    try:
        return apply_tree_edit(
            config_dir,
            "reset",
            {
                "type": body.type,
                "key": body.key,
                **({"path": body.path} if body.path else {}),
            },
            lambda: reinitialize_instrument(
                config_dir, body.type, body.key, path=body.path
            ),
        )
    except Exception as e:
        logger.exception("Reset instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/manage-instruments/removal-impact")
def api_removal_impact(body: _RemoveBody, env: Env = Depends(get_env)):
    """What breaks if this instrument is removed.

    A rule referencing a vanished attribute fails closed — it denies everything
    it covered — so removing one instrument can make a *different* one
    un-callable. Surfaced before the confirm, not discovered later.
    """
    config_dir = workspace_config_dir(env)
    try:
        attributes = attributes_under(config_dir, body.type, body.key, path=body.path)
        return {
            "attributes": sorted(attributes),
            "rules": rules_referencing(config_dir, attributes) if attributes else [],
            # A project names its instruments and resolves them when it runs, so
            # removing one breaks every project that uses it.
            "projects": projects_referencing(workspace_projects_dir(env), attributes),
        }
    except Exception as e:  # noqa: BLE001 - the dialog must still open
        logger.warning("Could not compute removal impact: %s", e)
        return {"attributes": [], "rules": [], "projects": [], "error": str(e)}


@router.post("/api/manage-instruments/remove")
def api_remove_instrument(body: _RemoveBody, env: Env = Depends(get_env)):
    """Remove an instrument from config."""
    config_dir = workspace_config_dir(env)
    try:
        return apply_tree_edit(
            config_dir,
            "remove",
            {
                "type": body.type,
                "key": body.key,
                **({"path": body.path} if body.path else {}),
            },
            lambda: remove_instrument(config_dir, body.type, body.key, path=body.path),
        )
    except Exception as e:
        logger.exception("Remove instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


class _DiscoverBody(BaseModel):
    type: str
    action: str
    params: dict = Field(default_factory=dict)
    # Resolved ancestor chain, ordered root-first.
    # e.g. [{"type": "prologix_gpib", "key": "a1b2c3d4"}]
    parent_chain: list[dict] = Field(default_factory=list)
    draft_id: str | None = None


@router.post("/api/manage-instruments/discover")
def api_discover(body: _DiscoverBody, env: Env = Depends(get_env)):
    """Run a discovery action defined on an instrument's Params class."""

    cls = load_params_class(body.type)
    actions = {a.name: a for a in getattr(cls, "discovery_actions", lambda: [])()}
    action = actions.get(body.action)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail=f"Type '{body.type}' has no discovery action '{body.action}'",
        )

    try:
        draft_chain = (
            _instrument_drafts.chain(workspace_config_dir(env), body.draft_id)
            if body.draft_id
            else None
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    def _in_process() -> dict:
        if draft_chain:
            registry, path = draft_discovery_tree(workspace_config_dir(env), draft_chain)
        else:
            registry = InstrumentRegistry.from_config_dir(workspace_config_dir(env))
            path = body.parent_chain
        return discover_with_registry(
            registry, path, body.type, body.action, body.params
        )

    try:
        # If a server is running it owns the transport, so it runs the scan —
        # two processes on one serial handle is exactly what this avoids, and
        # it keeps the permission gate's view of the hardware complete.
        return run_discovery(
            workspace_config_dir(env),
            type=body.type,
            action=body.action,
            params=body.params,
            parent_chain=body.parent_chain,
            in_process_fallback=_in_process,
            draft_chain=draft_chain,
        )
    except HTTPException:
        logger.exception(
            "Discovery action failed (HTTPException): %s/%s parent_chain=%s",
            body.type,
            body.action,
            body.parent_chain,
        )
        raise
    except Exception as e:
        logger.exception(
            "Discovery action failed: %s/%s — %s", body.type, body.action, e
        )
        raise HTTPException(status_code=400, detail=str(e))


class _ApplyChildrenBody(BaseModel):
    parent_type: str
    parent_key: str
    children: list[dict]
    path: list[dict[str, str]] | None = None


@router.post("/api/manage-instruments/apply-children")
def api_apply_children(body: _ApplyChildrenBody, env: Env = Depends(get_env)):
    config_dir = workspace_config_dir(env)
    payload = body.model_dump()
    try:
        return apply_tree_edit(
            config_dir,
            "apply_children",
            payload,
            lambda: apply_discovered_children(config_dir, **payload),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc




@router.post("/api/manage-instruments/drafts")
def api_create_instrument_draft(env: Env = Depends(get_env)):
    try:
        return {"id": _instrument_drafts.create(workspace_config_dir(env))}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/api/manage-instruments/drafts/{draft_id}")
def api_stage_instrument_draft(
    draft_id: str, body: _AddBody, env: Env = Depends(get_env)
):
    try:
        return _instrument_drafts.stage(
            workspace_config_dir(env), draft_id, [s.model_dump() for s in body.chain]
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/api/manage-instruments/drafts/{draft_id}")
def api_cancel_instrument_draft(draft_id: str, env: Env = Depends(get_env)):
    try:
        _instrument_drafts.cancel(workspace_config_dir(env), draft_id)
        return {"status": "ok"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/manage-instruments/drafts/{draft_id}/commit")
def api_commit_instrument_draft(draft_id: str, env: Env = Depends(get_env)):
    config_dir = workspace_config_dir(env)
    try:
        return _instrument_drafts.commit(
            config_dir,
            draft_id,
            lambda chain: apply_tree_edit(
                config_dir,
                "add",
                {"chain": chain},
                lambda: add_instrument_chain(config_dir, chain),
            ),
        )
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
