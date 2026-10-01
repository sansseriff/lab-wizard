"""Servers: this machine's instrument servers, claims, permissions, and remote servers."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lab_wizard.lib.utilities.config_io import (
    get_configured_tree,
)
from lab_wizard.wizard.backend.deps import (
    get_env,
    workspace_config_dir,
)
from lab_wizard.wizard.backend.hardware_access import (
    hardware_owner,
)
from lab_wizard.wizard.backend.models import Env
from lab_wizard.wizard.backend.permissions_api import (
    get_permissions_model,
    save_permissions,
)
from lab_wizard.wizard.backend.remote_servers import (
    add_remote_server,
    load_remote_servers,
    remove_remote_server,
)
from lab_wizard.wizard.backend.remote_servers import (
    test_connection as test_remote_connection,
)
from lab_wizard.wizard.backend.remote_tree import (
    remote_discover,
    remote_events,
    remote_tree,
    remote_tree_edit,
    server_calls,
)
from lab_wizard.wizard.backend.server_control import (
    disable_hosting,
    enable_hosting,
    restart_server,
    server_status,
    set_server_bind,
    start_server,
    stop_server,
    suggest_free_bind,
)
from lab_wizard.lib.client import server_registry
from lab_wizard.lib.client.session import Session

logger = logging.getLogger("lab_wizard.wizard.backend.routes.servers")
router = APIRouter()


@router.get("/api/local-servers")
def api_local_servers(env: Env = Depends(get_env)):
    """Every instrument server running on this machine.

    Not just this workspace's. A server started from another workspace holds
    real hardware and its endpoint is not derivable from here, so the UI needs
    the machine-local registry to say which workspace owns which rack.
    """

    # Flagged so the UI can distinguish "our own server" from "another
    # workspace's" — the two mean different things to a user, and only the
    # latter has a tree they cannot already see under Manage Instruments.
    own = str(env.config_dir) if env.config_dir else None
    return {
        "servers": [
            {
                **entry,
                "endpoints": server_registry.local_server_endpoints(entry),
                "is_this_workspace": entry.get("config_dir") == own,
            }
            for entry in server_registry.list_local_servers()
        ]
    }


@router.get("/api/local-servers/claims")
def api_local_server_claims():
    """Run claims held on every instrument server on this machine.

    A claim is how a running measurement keeps other runs off the instruments
    it drives (``plans/server_plan.md`` Phase 9), so this answers "why was my
    run refused" and "is something still holding that counter". A server that
    does not answer is reported rather than raised: one stopped daemon must not
    blank the page.
    """

    out = []
    for entry in server_registry.list_local_servers():
        endpoints = server_registry.local_server_endpoints(entry)
        if not endpoints:
            continue
        url = endpoints[0]
        row = {
            "url": url,
            "pid": entry.get("pid"),
            "workspace_path": entry.get("workspace_path"),
            "claims": [],
            "error": None,
        }
        try:
            session = Session(url, timeout_ms=2_000, auto_reconnect=False)
            try:
                row["claims"] = session.call("claim_list") or []
            finally:
                session.close()
        except Exception as e:  # noqa: BLE001 - reported per server
            row["error"] = str(e)
        out.append(row)
    return {"servers": out}


class _ForceReleaseClaimRequest(BaseModel):
    url: str
    unit: str


@router.post("/api/local-servers/claims/force-release")
def api_force_release_claim(req: _ForceReleaseClaimRequest):
    """End every claim touching ``unit`` on one server.

    For a run that is stuck, or whose client disappeared. The server resets the
    released instruments to baseline before anyone else can claim them.
    """

    try:
        session = Session(req.url, timeout_ms=10_000)
        try:
            result = session.call("claim_force_release", {"unit": req.unit})
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", **(result or {})}


class _ReleaseRequest(BaseModel):
    url: str
    path: str


@router.post("/api/local-servers/release")
def api_release_hardware(req: _ReleaseRequest):
    """Ask a server to disconnect and evict one root.

    Hands a rack back without stopping the whole server, which is what a user
    wants when a local project needs the bus. The path stays servable — the next
    call through the server reopens it.
    """

    try:
        session = Session(req.url, timeout_ms=10_000)
        try:
            released = session.call("release", {"path": req.path})
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "released": released}


class _RemoteTreeRequest(BaseModel):
    config_dir: str


class _RemoteEditRequest(BaseModel):
    config_dir: str
    operation: str
    payload: dict = Field(default_factory=dict)


@router.post("/api/remote-tree")
def api_remote_tree(req: _RemoteTreeRequest):
    """Tree, schema and recent activity of another workspace's server.

    The schema comes from that server, not this build: it decides which
    instrument types exist and what fields they take.
    """
    try:
        return remote_tree(req.config_dir)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/remote-tree/edit")
def api_remote_tree_edit(req: _RemoteEditRequest):
    """Add, remove, or reset an instrument on another workspace's server.

    The server enforces both rules that matter — same-machine only, and not
    while the rack is open — so a refusal here is its answer, not ours.
    """
    try:
        return remote_tree_edit(req.config_dir, req.operation, req.payload)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


class _RemoteDiscoverRequest(BaseModel):
    config_dir: str
    type: str
    action: str
    params: dict = Field(default_factory=dict)
    parent_chain: list = Field(default_factory=list)


@router.post("/api/remote-tree/discover")
def api_remote_discover(req: _RemoteDiscoverRequest):
    """Run a discovery scan on another workspace's server.

    The scan has to happen where the hardware is. Without it, adding an
    instrument to another workspace would mean knowing its bus address by heart.
    """
    try:
        return remote_discover(
            req.config_dir,
            type=req.type,
            action=req.action,
            params=req.params,
            parent_chain=req.parent_chain,
        )
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/remote-tree/events")
def api_remote_events(req: _RemoteTreeRequest):
    """Recent notable events recorded by that server."""
    try:
        return {"events": remote_events(req.config_dir)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/hardware-owner")
def api_hardware_owner(env: Env = Depends(get_env)):
    """Which process currently owns this workspace's hardware.

    ``server`` means the wizard routes hardware operations through it; the UI
    should say so, since it explains why discovery behaves differently.
    """
    return hardware_owner(workspace_config_dir(env))


@router.get("/api/permissions")
def api_get_permissions(env: Env = Depends(get_env)):
    """Return the local instrument tree + permission vocabulary + current rules.

    ``tree`` mirrors manage-instruments; ``instruments`` carries the state keys
    and methods the rule builder offers per node; ``permissions`` is the current
    ``permissions:`` block from server.yaml.
    """
    config_dir = workspace_config_dir(env)
    model = get_permissions_model(config_dir)
    return {"tree": get_configured_tree(config_dir), **model}


class _SavePermissionsRequest(BaseModel):
    permissions: dict = Field(default_factory=dict)


@router.put("/api/permissions")
def api_save_permissions(req: _SavePermissionsRequest, env: Env = Depends(get_env)):
    """Validate and persist the ``permissions:`` block to server.yaml."""
    config_dir = workspace_config_dir(env)
    try:
        saved = save_permissions(config_dir, req.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "permissions": saved}


# -------------------- Remote Servers (consuming side) --------------------


class _RemoteServerRequest(BaseModel):
    name: str
    url: str


class _RemoteTestRequest(BaseModel):
    url: str


@router.get("/api/remote-servers")
def api_get_remote_servers(env: Env = Depends(get_env)):
    """Return the registered remote servers (the client address book)."""
    return {"servers": load_remote_servers(workspace_config_dir(env))}


@router.post("/api/remote-servers")
def api_add_remote_server(req: _RemoteServerRequest, env: Env = Depends(get_env)):
    """Add (or update by name) a remote server."""
    try:
        servers = add_remote_server(workspace_config_dir(env), req.name.strip(), req.url.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "servers": servers}


@router.delete("/api/remote-servers/{name}")
def api_remove_remote_server(name: str, env: Env = Depends(get_env)):
    """Remove a remote server by name."""
    return {"status": "ok", "servers": remove_remote_server(workspace_config_dir(env), name)}


@router.post("/api/remote-servers/test")
def api_test_remote_server(req: _RemoteTestRequest):
    """Live-test a remote server URL and return its attributes (never errors)."""
    return test_remote_connection(req.url.strip())


# -------------------- Instrument Server lifecycle (hosting side) --------------------


class _ServerStartRequest(BaseModel):
    # When true, the server is detached and survives wizard close (daemon).
    detached: bool = False


@router.get("/api/server/status")
def api_server_status(env: Env = Depends(get_env)):
    """Return whether this workstation's instrument server is running."""
    return server_status(workspace_config_dir(env))


@router.get("/api/server/calls")
def api_server_calls(limit: int = 50, env: Env = Depends(get_env)):
    """What this workspace's server is routing now, and just routed.

    ``state`` says why there may be nothing to show: ``stopped`` (no server),
    ``outdated`` (a server started before it kept a call log; restarting it
    fixes that), or ``error``.
    """
    try:
        return {"state": "ok", **server_calls(str(workspace_config_dir(env)), limit)}
    except ValueError:
        return {"state": "stopped"}
    except Exception as e:  # noqa: BLE001 - shown in place of the list
        if "calls_recent" in str(e) or "not found" in str(e).lower():
            return {"state": "outdated"}
        return {"state": "error", "error": str(e)}


@router.post("/api/server/enable-hosting")
def api_enable_hosting(env: Env = Depends(get_env)):
    """Make this workspace a hardware host, serving this machine over ipc.

    Creating the server config is the opt-in; a workspace without one is a
    client, which is what keeps a cloned workspace from racing the host for the
    same instruments. No address is chosen — add a bind only when another
    machine needs to connect.
    """
    return enable_hosting(workspace_config_dir(env))


@router.post("/api/server/disable-hosting")
def api_disable_hosting(env: Env = Depends(get_env)):
    """Stop hosting and return this workspace to being a client."""
    try:
        return disable_hosting(workspace_config_dir(env))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/server/start")
def api_server_start(req: _ServerStartRequest, env: Env = Depends(get_env)):
    """Start the instrument server (managed child or detached daemon)."""
    try:
        return start_server(workspace_config_dir(env), detached=req.detached)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/server/stop")
def api_server_stop(env: Env = Depends(get_env)):
    """Stop the running instrument server (any mode)."""
    return stop_server(workspace_config_dir(env))


@router.post("/api/server/restart")
def api_server_restart(req: _ServerStartRequest, env: Env = Depends(get_env)):
    """Restart the server — used to apply edited permission rules."""
    try:
        return restart_server(workspace_config_dir(env), detached=req.detached)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class _ServerBindRequest(BaseModel):
    bind: str


@router.get("/api/server/suggest-port")
def api_server_suggest_port(prefer_default: bool = False, env: Env = Depends(get_env)):
    """Return a tcp://host:port bind on a currently-free port (not persisted).

    ``prefer_default=true`` offers the standard/existing port first when free —
    used for the initial suggestion before the server is configured.
    """
    return {"bind": suggest_free_bind(workspace_config_dir(env), prefer_default=prefer_default)}


@router.put("/api/server/bind")
def api_server_set_bind(req: _ServerBindRequest, env: Env = Depends(get_env)):
    """Persist this workstation's server bind address."""
    try:
        return set_server_bind(workspace_config_dir(env), req.bind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
