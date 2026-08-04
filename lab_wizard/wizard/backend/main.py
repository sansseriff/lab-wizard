from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import asyncio
import multiprocessing
import logging
import os
import sys
from fastapi.staticfiles import StaticFiles
from fastapi import Depends
import time
import urllib.request

try:
    import webview  # type: ignore
except Exception:  # ImportError or runtime issues shouldn't block headless mode
    webview = None  # type: ignore
import tempfile
from multiprocessing.connection import Connection
from fastapi import HTTPException
import argparse
from typing import Any
from uvicorn import Config, Server
from uuid import uuid4
from lab_wizard.wizard.backend.models import (
    Env,
    OutputReq,
    ConfiguredResource,
    RemoteMatch,
)
from lab_wizard.wizard.backend.utils_runtime import (
    has_gui_context,
    green,
    get_ipv4_addresses,
    is_ssh_session,
)


from lab_wizard.wizard.backend.get_measurements import (
    get_measurements,
    reqs_from_measurement,
    discover_matching_instruments,
)
from lab_wizard.lib.utilities.config_io import (
    get_configured_tree,
    add_instrument_chain,
    reinitialize_instrument,
    remove_instrument,
    load_instruments,
    save_instruments_to_config,
    instrument_hash,
    assign_missing_leaf_attribute_names,
)
from lab_wizard.lib.utilities.flat_resource_io import (
    add_resource as _flat_add_resource,
    reset_resource as _flat_reset_resource,
    remove_resource as _flat_remove_resource,
    update_resource_fields as _flat_update_resource_fields,
    get_configured_resources_tree,
)
from lab_wizard.lib.utilities.params_discovery import (
    get_instrument_metadata,
    get_saver_metadata,
    get_plotter_metadata,
)
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    generate_measurement_project,
)
from lab_wizard.wizard.backend.custom_resource_generation import (
    GenerateCustomResourceRequest,
    generate_custom_resource_project,
)
from lab_wizard.wizard.backend.permissions_api import (
    attributes_under,
    get_permissions_model,
    rules_referencing,
    save_permissions,
)
from lab_wizard.wizard.backend.remote_servers import (
    load_remote_servers,
    add_remote_server,
    remove_remote_server,
    test_connection as test_remote_connection,
    list_remote_attributes,
)
from lab_wizard.wizard.backend.server_control import (
    ensure_server,
    server_status,
    start_server,
    stop_server,
    restart_server,
    stop_managed_children,
    set_server_bind,
    suggest_free_bind,
)
from pydantic import BaseModel as _PermBM, Field as _PermField
from pathlib import Path
from lab_wizard.wizard.backend.hardware_access import hardware_owner, run_discovery
from lab_wizard.wizard.backend.remote_tree import (
    remote_events,
    remote_tree,
    remote_tree_edit,
)
from lab_wizard.wizard.backend.transport_status import (
    conflicts_for_selection,
    transport_overview,
)
from lab_wizard.wizard.backend.logging_config import configure_wizard_logging


from lab_wizard.wizard.backend.location import WEB_DIR

FRAMELESS = False
ICON_PATH = Path(WEB_DIR) / "icon.png"
logger = logging.getLogger("lab_wizard.wizard.backend.main")


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    env = Env.from_current_workspace()
    if env.logs_dir is None:
        raise RuntimeError("Workspace logs directory was not resolved")
    log_file = configure_wizard_logging(logs_dir=env.logs_dir)
    logger.info("Wizard logging initialized at %s", log_file)
    app.state.env = env

    # Pre-warm the instrument metadata cache in a thread so the first
    # /api/manage-instruments request is instant.  We do this BEFORE yielding
    # so the server only starts accepting connections once the cache is hot —
    # the health-poll in start_window therefore returns OK at exactly the
    # right moment.
    await asyncio.to_thread(get_instrument_metadata)

    # Find-or-start this workspace's instrument server, so hardware has exactly
    # one owner and the wizard is a client of it. Off the event loop because
    # start_server waits briefly to confirm the child survived. Managed mode:
    # stop_managed_children() below stops it when the wizard exits.
    try:
        status = await asyncio.to_thread(ensure_server, str(env.config_dir))
        if status.get("running"):
            logger.info(
                "Instrument server owns hardware for this workspace (pid=%s, bind=%s)",
                status.get("pid"),
                status.get("bind"),
            )
        else:
            logger.info(
                "No instrument server for this workspace; the wizard will open "
                "hardware in-process"
            )
    except Exception:
        logger.exception("Could not reconcile the instrument server; continuing")

    yield
    # Code to run on shutdown (if any)
    try:
        # Stop any instrument server we launched in managed mode; detached
        # (daemon) servers are intentionally left running.
        stop_managed_children()
    except Exception as e:
        logger.exception("Unhandled shutdown error: %s", e)


def get_env(request: Request) -> Env:
    """Dependency to provide process-wide Env stored on app.state."""
    env = getattr(request.app.state, "env", None)
    if env is None:
        # Fallback: create once if not present (e.g., during tests)
        env = Env.from_current_workspace()
        request.app.state.env = env
    return env


# Pass the lifespan manager to the FastAPI app
app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = uuid4().hex[:12]
    start = time.perf_counter()
    logger.debug(
        "request.start id=%s %s %s", request_id, request.method, request.url.path
    )
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request.error id=%s %s %s", request_id, request.method, request.url.path
        )
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    logger.info(
        "request.done id=%s status=%s %s %s %.1fms",
        request_id,
        response.status_code,
        request.method,
        request.url.path,
        elapsed_ms,
    )
    response.headers["X-Request-ID"] = request_id
    return response


class UvicornServer(multiprocessing.Process):
    def __init__(self, config: Config):
        super().__init__()
        self.server = Server(config=config)
        self.config = config

    def stop(self):
        self.terminate()

    def run(self):
        # print("running server")
        self.server.run()


# NOTE: Mount StaticFiles AFTER declaring API routes so it doesn't intercept /api/*


@app.get("/api/health")
def health(request: Request):
    """Liveness probe, and proof of *which* wizard is answering.

    The workspace is included because several wizards can run on one machine.
    Without it, a second instance whose port was taken would happily open a
    window onto the first instance's server and show the wrong workspace's
    instruments — which looks like a working app, not a failure.
    """
    env = getattr(request.app.state, "env", None)
    return {
        "status": "ok",
        "workspace": str(getattr(env, "root", "") or ""),
        "pid": os.getpid(),
    }


# --- Placeholder API routes for frontend pages ---
@app.get("/api/get-measurements")
def get_measurements_meta(env: Env = Depends(get_env), verbose: bool = False):
    res = get_measurements(env)
    if verbose:
        logger.info("Measurements metadata requested: %s", list(res.keys()))
    return res


@app.get("/api/get-resources/{name}")
def get_resources(
    name: str,
    env: Env = Depends(get_env),
    verbose: bool = False,
):
    """Return all required resources (instruments, savers, plotters) for a measurement.

    Each entry carries a ``resource_kind`` discriminator and the relevant
    matching list — ``matching_instruments`` for instrument requirements
    (populated by class-hierarchy discovery) or ``matching_resources`` for
    saver/plotter requirements (populated from the configured registry).
    """
    logger.info("Getting resources for measurement '%s'", name)

    all_meas = get_measurements(env)
    if name not in all_meas:
        raise HTTPException(status_code=404, detail=f"Unknown measurement: {name}")

    choice = all_meas[name]
    if verbose:
        logger.debug("Measurement choice for '%s': %s", name, choice)

    config_dir = _config_dir(env)

    try:
        reqs = reqs_from_measurement(choice)

        # Pre-load saver/plotter registries once.
        saver_tree = get_configured_resources_tree(config_dir, "saver")
        plotter_tree = get_configured_resources_tree(config_dir, "plotter")

        # Enumerate remote attributes once (best-effort; unreachable servers are
        # skipped) so each instrument requirement can offer remote matches.
        try:
            remote_attrs = list_remote_attributes(config_dir)
        except Exception as e:
            logger.warning("Could not enumerate remote attributes: %s", e)
            remote_attrs = []

        outputs: list[OutputReq] = []
        for req in reqs:
            if req.resource_kind == "instrument":
                try:
                    matches = discover_matching_instruments(env, req.base_type)
                except Exception as e:
                    logger.exception(
                        "Discovery error for requirement '%s': %s", req.variable_name, e
                    )
                    matches = []
                base_name = getattr(req.base_type, "__name__", str(req.base_type))
                remote_matches = [
                    RemoteMatch(**attr)
                    for attr in remote_attrs
                    if attr.get("behavior_abc") == base_name and attr.get("attribute")
                ]
                outputs.append(
                    OutputReq(
                        variable_name=req.variable_name,
                        base_type=str(req.base_type),
                        resource_kind="instrument",
                        is_list=req.is_list,
                        matching_instruments=matches,
                        matching_resources=[],
                        matching_remote=remote_matches,
                    )
                )
            elif req.resource_kind in ("saver", "plotter"):
                tree = saver_tree if req.resource_kind == "saver" else plotter_tree
                outputs.append(
                    OutputReq(
                        variable_name=req.variable_name,
                        base_type=str(req.base_type),
                        resource_kind=req.resource_kind,
                        is_list=req.is_list,
                        matching_instruments=[],
                        matching_resources=[
                            ConfiguredResource(
                                type=item["type"],
                                key=item["key"],
                                fields=item["fields"],
                            )
                            for item in tree
                        ],
                    )
                )

        if verbose:
            logger.debug("Final resource requirements for '%s': %s", name, outputs)
        return outputs

    except Exception as e:
        logger.exception("Error getting resources for measurement '%s': %s", name, e)
        return {"error": str(e)}


# -------------------- Manage Instruments --------------------


def _config_dir(env: Env) -> str:
    if env.config_dir is None:
        raise RuntimeError("Workspace config directory was not resolved")
    return str(env.config_dir)


def _projects_dir(env: Env) -> Path:
    if env.projects_dir is None:
        raise RuntimeError("Workspace projects directory was not resolved")
    return env.projects_dir


@app.get("/api/manage-instruments")
def api_manage_instruments(env: Env = Depends(get_env)):
    """Return the configured tree and metadata for all discoverable types."""
    config_dir = _config_dir(env)
    tree = get_configured_tree(config_dir)
    metadata = get_instrument_metadata()
    return {"tree": tree, "metadata": metadata}


@app.get("/api/transport-status")
def api_transport_status(env: Env = Depends(get_env)):
    """Per-root sharing/authority declarations plus what the server holds now.

    Lets the tree show whether a rack is exclusive or shared, and whether it is
    currently in use, without the user having to run anything to find out.
    """
    return transport_overview(_config_dir(env))


class _ConflictCheckRequest(_PermBM):
    paths: list[str] = _PermField(default_factory=list)


@app.post("/api/transport-status/check")
def api_transport_conflicts(
    req: _ConflictCheckRequest, env: Env = Depends(get_env)
):
    """Would a *local* project using these instruments contend with the server?

    Used during measurement creation so a conflict is a design-time answer
    rather than a 2am failure.
    """
    return conflicts_for_selection(_config_dir(env), req.paths)


@app.get("/api/local-servers")
def api_local_servers():
    """Every instrument server running on this machine.

    Not just this workspace's. A server started from another workspace holds
    real hardware and its endpoint is not derivable from here, so the UI needs
    the machine-local registry to say which workspace owns which rack.
    """
    from lab_wizard.lib.client.server_registry import (
        list_local_servers,
        local_server_endpoints,
    )

    return {
        "servers": [
            {**entry, "endpoints": local_server_endpoints(entry)}
            for entry in list_local_servers()
        ]
    }


class _ReleaseRequest(_PermBM):
    url: str
    path: str


@app.post("/api/local-servers/release")
def api_release_hardware(req: _ReleaseRequest):
    """Ask a server to disconnect and evict one root.

    Hands a rack back without stopping the whole server, which is what a user
    wants when a local project needs the bus. The path stays servable — the next
    call through the server reopens it.
    """
    from lab_wizard.lib.client.session import Session

    try:
        session = Session(req.url, timeout_ms=10_000)
        try:
            released = session.call("release", {"path": req.path})
        finally:
            session.close()
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "released": released}


class _RemoteTreeRequest(_PermBM):
    config_dir: str


class _RemoteEditRequest(_PermBM):
    config_dir: str
    operation: str
    payload: dict = _PermField(default_factory=dict)


@app.post("/api/remote-tree")
def api_remote_tree(req: _RemoteTreeRequest):
    """Tree, schema and recent activity of another workspace's server.

    The schema comes from that server, not this build: it decides which
    instrument types exist and what fields they take.
    """
    try:
        return remote_tree(req.config_dir)
    except Exception as e:  # noqa: BLE001 - surfaced to the UI
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/remote-tree/edit")
def api_remote_tree_edit(req: _RemoteEditRequest):
    """Add, remove, or reset an instrument on another workspace's server.

    The server enforces both rules that matter — same-machine only, and not
    while the rack is open — so a refusal here is its answer, not ours.
    """
    try:
        return remote_tree_edit(req.config_dir, req.operation, req.payload)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/remote-tree/events")
def api_remote_events(req: _RemoteTreeRequest):
    """Recent notable events recorded by that server."""
    try:
        return {"events": remote_events(req.config_dir)}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/hardware-owner")
def api_hardware_owner(env: Env = Depends(get_env)):
    """Which process currently owns this workspace's hardware.

    ``server`` means the wizard routes hardware operations through it; the UI
    should say so, since it explains why discovery behaves differently.
    """
    return hardware_owner(_config_dir(env))


@app.get("/api/permissions")
def api_get_permissions(env: Env = Depends(get_env)):
    """Return the local instrument tree + permission vocabulary + current rules.

    ``tree`` mirrors manage-instruments; ``instruments`` carries the state keys
    and methods the rule builder offers per node; ``permissions`` is the current
    ``permissions:`` block from server.yaml.
    """
    config_dir = _config_dir(env)
    model = get_permissions_model(config_dir)
    return {"tree": get_configured_tree(config_dir), **model}


class _SavePermissionsRequest(_PermBM):
    permissions: dict = _PermField(default_factory=dict)


@app.put("/api/permissions")
def api_save_permissions(req: _SavePermissionsRequest, env: Env = Depends(get_env)):
    """Validate and persist the ``permissions:`` block to server.yaml."""
    config_dir = _config_dir(env)
    try:
        saved = save_permissions(config_dir, req.permissions)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "permissions": saved}


# -------------------- Remote Servers (consuming side) --------------------


class _RemoteServerRequest(_PermBM):
    name: str
    url: str


class _RemoteTestRequest(_PermBM):
    url: str


@app.get("/api/remote-servers")
def api_get_remote_servers(env: Env = Depends(get_env)):
    """Return the registered remote servers (the client address book)."""
    return {"servers": load_remote_servers(_config_dir(env))}


@app.post("/api/remote-servers")
def api_add_remote_server(req: _RemoteServerRequest, env: Env = Depends(get_env)):
    """Add (or update by name) a remote server."""
    try:
        servers = add_remote_server(_config_dir(env), req.name.strip(), req.url.strip())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "ok", "servers": servers}


@app.delete("/api/remote-servers/{name}")
def api_remove_remote_server(name: str, env: Env = Depends(get_env)):
    """Remove a remote server by name."""
    return {"status": "ok", "servers": remove_remote_server(_config_dir(env), name)}


@app.post("/api/remote-servers/test")
def api_test_remote_server(req: _RemoteTestRequest):
    """Live-test a remote server URL and return its attributes (never errors)."""
    return test_remote_connection(req.url.strip())


# -------------------- Instrument Server lifecycle (hosting side) --------------------


class _ServerStartRequest(_PermBM):
    # When true, the server is detached and survives wizard close (daemon).
    detached: bool = False


@app.get("/api/server/status")
def api_server_status(env: Env = Depends(get_env)):
    """Return whether this workstation's instrument server is running."""
    return server_status(_config_dir(env))


@app.post("/api/server/start")
def api_server_start(req: _ServerStartRequest, env: Env = Depends(get_env)):
    """Start the instrument server (managed child or detached daemon)."""
    try:
        return start_server(_config_dir(env), detached=req.detached)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/server/stop")
def api_server_stop(env: Env = Depends(get_env)):
    """Stop the running instrument server (any mode)."""
    return stop_server(_config_dir(env))


@app.post("/api/server/restart")
def api_server_restart(req: _ServerStartRequest, env: Env = Depends(get_env)):
    """Restart the server — used to apply edited permission rules."""
    try:
        return restart_server(_config_dir(env), detached=req.detached)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class _ServerBindRequest(_PermBM):
    bind: str


@app.get("/api/server/suggest-port")
def api_server_suggest_port(prefer_default: bool = False, env: Env = Depends(get_env)):
    """Return a tcp://host:port bind on a currently-free port (not persisted).

    ``prefer_default=true`` offers the standard/existing port first when free —
    used for the initial suggestion before the server is configured.
    """
    return {"bind": suggest_free_bind(_config_dir(env), prefer_default=prefer_default)}


@app.put("/api/server/bind")
def api_server_set_bind(req: _ServerBindRequest, env: Env = Depends(get_env)):
    """Persist this workstation's server bind address."""
    try:
        return set_server_bind(_config_dir(env), req.bind)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


from pydantic import BaseModel as _BM, Field as _Field
from typing import List as _List


class _ChainStep(_BM):
    type: str
    key: str
    action: str  # "create_new" | "use_existing"
    extra: dict = _Field(
        default_factory=dict
    )  # optional extra fields to set on newly-created params


class _AddBody(_BM):
    chain: _List[_ChainStep]


class _ResetBody(_BM):
    type: str
    key: str


class _RemoveBody(_BM):
    type: str
    key: str


@app.post("/api/manage-instruments/add")
def api_add_instrument(body: _AddBody, env: Env = Depends(get_env)):
    """Add an instrument (with optional parent chain creation)."""
    config_dir = _config_dir(env)
    try:
        chain_dicts = [s.model_dump() for s in body.chain]
        result = add_instrument_chain(config_dir, chain_dicts)
        return result
    except Exception as e:
        logger.exception("Add instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/manage-instruments/reset")
def api_reset_instrument(body: _ResetBody, env: Env = Depends(get_env)):
    """Reset an instrument's config to factory defaults (preserves children)."""
    config_dir = _config_dir(env)
    try:
        result = reinitialize_instrument(config_dir, body.type, body.key)
        return result
    except Exception as e:
        logger.exception("Reset instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/manage-instruments/removal-impact")
def api_removal_impact(body: _RemoveBody, env: Env = Depends(get_env)):
    """What breaks if this instrument is removed.

    A rule referencing a vanished attribute fails closed — it denies everything
    it covered — so removing one instrument can make a *different* one
    un-callable. Surfaced before the confirm, not discovered later.
    """
    config_dir = _config_dir(env)
    try:
        attributes = attributes_under(config_dir, body.type, body.key)
        return {
            "attributes": sorted(attributes),
            "rules": rules_referencing(config_dir, attributes) if attributes else [],
        }
    except Exception as e:  # noqa: BLE001 - the dialog must still open
        logger.warning("Could not compute removal impact: %s", e)
        return {"attributes": [], "rules": [], "error": str(e)}


@app.post("/api/manage-instruments/remove")
def api_remove_instrument(body: _RemoveBody, env: Env = Depends(get_env)):
    """Remove an instrument from config."""
    config_dir = _config_dir(env)
    try:
        result = remove_instrument(config_dir, body.type, body.key)
        return result
    except Exception as e:
        logger.exception("Remove instrument API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


class _DiscoverBody(_BM):
    type: str
    action: str
    params: dict = _Field(default_factory=dict)
    # Resolved ancestor chain, ordered root-first.
    # e.g. [{"type": "prologix_gpib", "key": "a1b2c3d4"}]
    parent_chain: list[dict] = _Field(default_factory=list)


def _walk_parent_chain(chain: list[dict], env: Env):
    """Walk a resolved ancestor chain top-down, initializing each level.

    ``chain`` is root-first: [{"type": "prologix_gpib", "key": "a1b2"}, ...]
    Returns the last (deepest) initialized instrument — the immediate parent
    of the discovery target.
    """
    config_dir = _config_dir(env)
    instruments = load_instruments(config_dir)

    root_key = chain[0]["key"]
    root_params = instruments.get(root_key)
    if root_params is None:
        raise HTTPException(
            404, f"Top-level {chain[0]['type']} ({root_key}) not found in config"
        )

    current_inst = root_params.create_inst()
    for step in chain[1:]:
        current_inst = current_inst.make_child(step["key"])

    return current_inst


@app.post("/api/manage-instruments/discover")
def api_discover(body: _DiscoverBody, env: Env = Depends(get_env)):
    """Run a discovery action defined on an instrument's Params class."""
    from lab_wizard.lib.utilities.params_discovery import load_params_class

    cls = load_params_class(body.type)
    actions = {a.name: a for a in cls.discovery_actions()}
    action = actions.get(body.action)
    if action is None:
        raise HTTPException(
            status_code=404,
            detail=f"Type '{body.type}' has no discovery action '{body.action}'",
        )

    def _in_process() -> dict:
        """Fallback for when no server owns this workspace's hardware."""
        parent_inst = None
        try:
            if body.parent_chain:
                parent_inst = _walk_parent_chain(body.parent_chain, env)
            return action.run(body.params, parent=parent_inst).model_dump()
        finally:
            if parent_inst is not None and hasattr(parent_inst, "disconnect"):
                parent_inst.disconnect()

    try:
        # If a server is running it owns the transport, so it runs the scan —
        # two processes on one serial handle is exactly what this avoids, and
        # it keeps the permission gate's view of the hardware complete.
        return run_discovery(
            _config_dir(env),
            type=body.type,
            action=body.action,
            params=body.params,
            parent_chain=body.parent_chain,
            in_process_fallback=_in_process,
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


class _ApplyChildrenBody(_BM):
    parent_type: str
    parent_key: str
    children: list[dict]  # [{type, key_fields: {slot?, gpib_address?, ...}}]


@app.post("/api/manage-instruments/apply-children")
def api_apply_children(body: _ApplyChildrenBody, env: Env = Depends(get_env)):
    """Add discovered children to an existing parent instrument in config."""
    from lab_wizard.lib.utilities.params_discovery import load_params_class

    config_dir = _config_dir(env)
    instruments = load_instruments(config_dir)

    parent = next(
        (
            p
            for p in instruments.values()
            if getattr(p, "type", None) == body.parent_type
        ),
        None,
    )
    if parent is None:
        raise HTTPException(
            status_code=404,
            detail=f"Parent {body.parent_type} ({body.parent_key}) not found",
        )

    added = []
    for child_spec in body.children:
        child_type = child_spec["type"]
        key_fields = child_spec.get("key_fields", {})
        if child_type is None:
            continue
        params_cls = load_params_class(child_type)
        child_params = params_cls()
        for field_name, field_val in key_fields.items():
            if hasattr(child_params, field_name):
                setattr(child_params, field_name, str(field_val))
        child_key = instrument_hash(child_type, *[str(v) for v in key_fields.values()])
        parent.children[child_key] = child_params
        added.append({"type": child_type, **key_fields})

    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)
    logger.info(
        "apply-children: added %d children to %s (%s)",
        len(added),
        body.parent_type,
        body.parent_key,
    )
    return {"status": "ok", "added": added, "tree": get_configured_tree(config_dir)}


# -------------------- Manage Savers / Plotters --------------------


class _AddResourceBody(_BM):
    type: str
    key: str
    fields: dict = _Field(default_factory=dict)


class _ResourceTypeKeyBody(_BM):
    type: str
    key: str


class _UpdateResourceBody(_BM):
    type: str
    key: str
    fields: dict


def _make_resource_endpoints(kind: str, metadata_fn):  # noqa: ANN001
    """Register four CRUD endpoints for a flat resource registry under
    /api/manage-{kind}s/...  ``metadata_fn`` returns the discovery metadata."""
    plural = f"{kind}s"

    @app.get(f"/api/manage-{plural}", name=f"api_manage_{plural}")
    def _list(env: Env = Depends(get_env)):
        config_dir = _config_dir(env)
        return {
            "tree": get_configured_resources_tree(config_dir, kind),  # type: ignore[arg-type]
            "metadata": metadata_fn(),
        }

    @app.post(f"/api/manage-{plural}/add", name=f"api_add_{kind}")
    def _add(body: _AddResourceBody, env: Env = Depends(get_env)):
        config_dir = _config_dir(env)
        try:
            return _flat_add_resource(config_dir, kind, body.type, body.key, body.fields)  # type: ignore[arg-type]
        except Exception as e:
            logger.exception("Add %s API failed: %s", kind, e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post(f"/api/manage-{plural}/reset", name=f"api_reset_{kind}")
    def _reset(body: _ResourceTypeKeyBody, env: Env = Depends(get_env)):
        config_dir = _config_dir(env)
        try:
            return _flat_reset_resource(config_dir, kind, body.type, body.key)  # type: ignore[arg-type]
        except Exception as e:
            logger.exception("Reset %s API failed: %s", kind, e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post(f"/api/manage-{plural}/remove", name=f"api_remove_{kind}")
    def _remove(body: _ResourceTypeKeyBody, env: Env = Depends(get_env)):
        config_dir = _config_dir(env)
        try:
            return _flat_remove_resource(config_dir, kind, body.type, body.key)  # type: ignore[arg-type]
        except Exception as e:
            logger.exception("Remove %s API failed: %s", kind, e)
            raise HTTPException(status_code=400, detail=str(e))

    @app.post(f"/api/manage-{plural}/update", name=f"api_update_{kind}")
    def _update(body: _UpdateResourceBody, env: Env = Depends(get_env)):
        config_dir = _config_dir(env)
        try:
            return _flat_update_resource_fields(
                config_dir, kind, body.type, body.key, body.fields  # type: ignore[arg-type]
            )
        except Exception as e:
            logger.exception("Update %s API failed: %s", kind, e)
            raise HTTPException(status_code=400, detail=str(e))


_make_resource_endpoints("saver", get_saver_metadata)
_make_resource_endpoints("plotter", get_plotter_metadata)


@app.post("/api/create-measurement-project")
def api_create_measurement_project(
    body: GenerateProjectRequest,
    env: Env = Depends(get_env),
):
    """Create a new timestamped project folder with subset YAML + setup code."""
    try:
        return generate_measurement_project(
            config_dir=Path(_config_dir(env)),
            projects_dir=_projects_dir(env),
            req=body,
        )
    except Exception as e:
        logger.exception("Create measurement project API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/create-custom-resource-project")
def api_create_custom_resource_project(
    body: GenerateCustomResourceRequest,
    env: Env = Depends(get_env),
):
    """Create a new timestamped project containing a programmatically built setup file."""
    try:
        return generate_custom_resource_project(
            config_dir=Path(_config_dir(env)),
            projects_dir=_projects_dir(env),
            req=body,
        )
    except Exception as e:
        logger.exception("Create custom resource project API failed: %s", e)
        raise HTTPException(status_code=400, detail=str(e))


# Serve SvelteKit static build from resolved directory at root (mounted last)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="frontend")


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _choose_port(requested: int | None, default: int = 8884) -> int:
    """Pick the port to serve on.

    An explicit ``--port`` is honoured and fails loudly if taken — the user
    asked for that port and silently using another would be worse. Otherwise the
    familiar default is preferred when free, falling back to an OS-assigned one
    so a second workspace opens its own wizard instead of colliding.
    """
    if requested is not None:
        if not _port_is_free(requested):
            raise SystemExit(
                f"Port {requested} is already in use — most likely by another "
                "Lab Wizard. Omit --port to have one chosen automatically."
            )
        return requested

    if _port_is_free(default):
        return default

    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    logger.info("Port %d is in use; serving this workspace on %d instead", default, port)
    return port


def _verify_own_server(port: int, workspace: str, timeout: float = 15.0) -> None:
    """Confirm the server on ``port`` is the one we just started.

    Guards the failure that looks like success: if our child died and something
    else holds the port, opening a window onto it would show another
    workspace's instruments with no indication anything was wrong.
    """
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://localhost:{port}/api/health", timeout=1
            ) as r:
                if r.status == 200:
                    import json as _json

                    last = _json.loads(r.read().decode())
                    if not workspace or last.get("workspace") == workspace:
                        return
                    raise SystemExit(
                        f"Port {port} is served by a different Lab Wizard "
                        f"(workspace {last.get('workspace')!r}), not this one "
                        f"({workspace!r}). This workspace's server failed to "
                        "start — check the log above for the reason."
                    )
        except SystemExit:
            raise
        except Exception:
            time.sleep(0.05)
    raise SystemExit(
        f"This workspace's wizard did not come up on port {port} within "
        f"{timeout:.0f}s. Check the log above for the reason."
    )


def _wait_for_server(url: str, timeout: float = 15.0) -> bool:
    """Poll a health URL until it returns 200 or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.05)
    return False


def _resolve_webview_icon_path(icon_path: Path, temp_dir: str) -> str | None:
    """Return a pywebview-compatible icon path for the current platform."""
    if not icon_path.is_file():
        logger.warning("Wizard icon not found: %s", icon_path)
        return None

    if not sys.platform.startswith("win"):
        return str(icon_path)

    try:
        from PIL import Image
    except ImportError:
        logger.warning(
            "Pillow unavailable; using PNG icon path on Windows: %s", icon_path
        )
        return str(icon_path)

    ico_path = Path(temp_dir) / "icon.ico"
    with Image.open(icon_path) as image:
        image.convert("RGBA").save(
            ico_path,
            format="ICO",
            sizes=[
                (16, 16),
                (24, 24),
                (32, 32),
                (48, 48),
                (64, 64),
                (128, 128),
                (256, 256),
            ],
        )

    return str(ico_path)


def start_window(pipe_send: Connection, url_to_load: str, debug: bool = False):
    if webview is None:
        raise RuntimeError("pywebview is not available; cannot start UI window")

    health_url = url_to_load.rstrip("/") + "/api/health"
    _wait_for_server(health_url)

    def on_closed():
        pipe_send.send("closed")

    _win: Any = webview.create_window(  # type: ignore
        "Lab Wizard",
        url=url_to_load,
        resizable=True,
        width=1200,
        height=700,
        frameless=FRAMELESS,
        easy_drag=False,
    )

    # webview.start(debug=False) # NOTE if this is activated, then you don't get graceful shutdown from hitting the close button. (on osx)
    # https://github.com/r0x0r/pywebview/issues/1496#issuecomment-2410471185

    # if FRAMELESS:
    #     win.events.before_load += add_buttons
    _win.events.closed += on_closed  # type: ignore[attr-defined]
    with tempfile.TemporaryDirectory(prefix="lab-wizard-webview-") as storage_dir:
        icon_path = _resolve_webview_icon_path(ICON_PATH, storage_dir)
        webview.start(
            storage_path=storage_dir,
            debug=debug,
            icon=icon_path,
        )
        _win.evaluate_js("window.special = 3")  # type: ignore[attr-defined]


def parse_arguments():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Switch Control Backend")
    parser.add_argument("--debug", action="store_true", help="Run in debug mode")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Do not spawn a desktop window; print a URL instead",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Port to bind the server. Omit to prefer 8884 and fall back to a "
            "free port if it is taken, so a second workspace gets its own "
            "wizard. An explicit port fails if unavailable."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    os.environ["LAB_WIZARD_LOG_LEVEL"] = "DEBUG" if args.debug else "INFO"
    runtime_env = Env.from_current_workspace()
    if runtime_env.logs_dir is None:
        raise RuntimeError("Workspace logs directory was not resolved")
    configure_wizard_logging(logs_dir=runtime_env.logs_dir, debug=args.debug)
    log_level = "debug" if args.debug else "info"

    server_ip = "0.0.0.0"
    webview_ip = "localhost"
    # Chosen here, in the parent, so the webview URL is guaranteed to match what
    # the child binds. Previously the port was assumed, and a second workspace
    # whose bind failed would open a window onto the *first* one's server.
    server_port = _choose_port(args.port)
    conn_recv, conn_send = multiprocessing.Pipe()
    # init_event = multiprocessing.Event()  # Create an Event object

    # Start server first
    # user 1 worker for easier data sharing
    # Use an import string so the child process can import the app without pickling it
    app_import_str = "lab_wizard.wizard.backend.main:app"
    config = Config(
        app_import_str, host=server_ip, port=server_port, log_level=log_level, workers=1
    )
    instance = UvicornServer(config=config)
    instance.start()

    # If port 0 (auto), we can't easily query the bound port from uvicorn.Server in this process
    # without IPC, so keep to explicit ports for now. If needed, add a pipe to report.
    url = f"http://{webview_ip}:{server_port}/"
    should_spawn_ui = (not args.no_ui) and has_gui_context()

    # Refuse to show a window until the server answering is demonstrably ours.
    _verify_own_server(server_port, str(runtime_env.root or ""))
    logger.info("Wizard serving %s on port %d", runtime_env.root, server_port)

    if should_spawn_ui:
        # Then start window
        windowsp = multiprocessing.Process(
            target=start_window,
            args=(conn_send, url, args.debug),
        )

        windowsp.start()

        window_status = ""
        while "closed" not in window_status:
            window_status = conn_recv.recv()
            logger.debug("Window status event: %s", window_status)

        instance.stop()
    else:
        # Headless/SSH/no-UI: wait until the server is actually ready, then print URLs.
        health_url = f"http://localhost:{server_port}/api/health"
        _wait_for_server(health_url)
        print("\nNo UI context detected or --no-ui set.")
        # Enumerate all non-loopback IPv4s and print URLs
        ips = get_ipv4_addresses()
        if ips:
            print(
                "Reachable URLs on this host (for remote access, let port 8884 through your firewall):"
            )
            for ip in ips:
                print("  ", green(f"http://{ip}:{server_port}/"))
        else:
            print("Could not determine host IPs; try using the hostname or SSH tunnel.")

        # Always include localhost for ssh tunnel scenarios
        print("Also available via localhost if you port-forward:")
        print("  ", green(url))

        if is_ssh_session():
            print("\nHint: create a tunnel from your local machine:")
            print("  ssh -N -L 8884:localhost:%d <user>@<remote-host>" % server_port)
            print("Then open:")
            print("  ", green("http://localhost:8884/"))

        print("\nPress Ctrl+C to stop the server.\n")
        try:
            instance.join()
        except KeyboardInterrupt:
            pass
        finally:
            instance.stop()
