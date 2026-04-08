from fastapi import FastAPI, Request
from contextlib import asynccontextmanager
import asyncio
import multiprocessing
import logging
import os
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
from lab_wizard.wizard.backend.models import Env, OutputReq
from lab_wizard.wizard.backend.utils_runtime import has_gui_context, green, get_ipv4_addresses, is_ssh_session


from lab_wizard.wizard.backend.get_measurements import get_measurements, reqs_from_measurement, discover_matching_instruments
from lab_wizard.lib.utilities.config_io import (
    get_configured_tree,
    add_instrument_chain,
    reinitialize_instrument,
    remove_instrument,
    load_instruments,
    save_instruments_to_config,
    instrument_hash,
)
from lab_wizard.lib.utilities.params_discovery import get_instrument_metadata
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    generate_measurement_project,
)
from pathlib import Path
from lab_wizard.wizard.backend.logging_config import configure_wizard_logging



from lab_wizard.wizard.backend.location import WEB_DIR, LOG_DIR

FRAMELESS = False
ICON_PATH = str(Path(__file__).parent / "static" / "icon.png")
logger = logging.getLogger("lab_wizard.wizard.backend.main")


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    log_file = configure_wizard_logging(logs_dir=Path(LOG_DIR))
    logger.info("Wizard logging initialized at %s", log_file)
    app.state.env = Env()

    # Pre-warm the instrument metadata cache in a thread so the first
    # /api/manage-instruments request is instant.  We do this BEFORE yielding
    # so the server only starts accepting connections once the cache is hot —
    # the health-poll in start_window therefore returns OK at exactly the
    # right moment.
    await asyncio.to_thread(get_instrument_metadata)

    yield
    # Code to run on shutdown (if any)
    # print("Application shutting down.")
    try:
        # run any shutdown actions here
        # app.state.services.cryo.cleanup()
        pass
    except Exception as e:
        logger.exception("Unhandled shutdown error: %s", e)


def get_env(request: Request) -> Env:
    """Dependency to provide process-wide Env stored on app.state."""
    env = getattr(request.app.state, "env", None)
    if env is None:
        # Fallback: create once if not present (e.g., during tests)
        env = Env()
        request.app.state.env = env
    return env


# Pass the lifespan manager to the FastAPI app
app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = uuid4().hex[:12]
    start = time.perf_counter()
    logger.debug("request.start id=%s %s %s", request_id, request.method, request.url.path)
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
def health():
    """Lightweight liveness probe used by start_window to detect server readiness."""
    return {"status": "ok"}


# --- Placeholder API routes for frontend pages ---
@app.get("/api/get-measurements")
def get_measurements_meta(env: Env = Depends(get_env), verbose: bool = False):
    res = get_measurements(env)
    if verbose:
        logger.info("Measurements metadata requested: %s", list(res.keys()))
    return res


@app.get("/api/get-instruments/{name}")
def get_instruments(
    name: str,
    env: Env = Depends(get_env),
    verbose: bool = False,
):
    """Return required instrument role names for a given measurement name.

    Example: /api/get-instruments/ivCurve
    The function will resolve the MeasurementInfo using the same logic as get_measurements.
    """

    logger.info("Getting instruments for measurement '%s'", name)


    # in order to keep pages stateless, we re-aquire all measurements and then select the requested one
    all_meas = get_measurements(env)
    if name not in all_meas:
        raise HTTPException(status_code=404, detail=f"Unknown measurement: {name}")

    choice = all_meas[name]
    if verbose:
        logger.debug("Measurement choice for '%s': %s", name, choice)


    try: 
        reqs = reqs_from_measurement(choice)

        for req in reqs:
            # Discover instruments implementing the required base type
            base_type = req.base_type
            # base_type may come as a typing alias; ensure we have a Type
            try:
                matches = discover_matching_instruments(env, base_type)
            except Exception as e:
                logger.exception(
                    "Discovery error for requirement '%s': %s", req.variable_name, e
                )
                matches = []

            req.matching_instruments = matches


        # convert reqs to OutputReq for JSON serialization
        # must convert req.base_type from type to str
        reqs = [OutputReq(
            variable_name=req.variable_name,
            base_type=str(req.base_type),
            matching_instruments=req.matching_instruments
        ) for req in reqs]


        if verbose:
            logger.debug("Final instrument requirements for '%s': %s", name, reqs)

        return reqs
    
    except Exception as e:
        logger.exception("Error getting requirements for measurement '%s': %s", name, e)
        # raise HTTPException(status_code=500, detail=f"Error getting requirements: {e}")
        return {"error": str(e)}
    





@app.get("/api/resources/meta")
def get_resources_meta(env: Env = Depends(get_env)):
    """Return placeholder metadata for the create custom resource page."""
    return {
        "types": [
            {"id": "instrument", "label": "Instrument"},
            {"id": "component", "label": "Component"},
        ]
    }


# -------------------- Manage Instruments --------------------

def _config_dir(env: Env) -> str:
    return str(env.base_dir.parent / "config")


def _projects_dir(env: Env) -> Path:
    # base_dir -> <repo>/lab_wizard/lib ; projects live at <repo>/projects
    return env.base_dir.parent.parent / "projects"


@app.get("/api/manage-instruments")
def api_manage_instruments(env: Env = Depends(get_env)):
    """Return the configured tree and metadata for all discoverable types."""
    config_dir = _config_dir(env)
    tree = get_configured_tree(config_dir)
    metadata = get_instrument_metadata()
    return {"tree": tree, "metadata": metadata}


from pydantic import BaseModel as _BM, Field as _Field
from typing import List as _List


class _ChainStep(_BM):
    type: str
    key: str
    action: str  # "create_new" | "use_existing"
    extra: dict = _Field(default_factory=dict)  # optional extra fields to set on newly-created params


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


@app.get("/api/manage-instruments/probe-dbay")
def api_probe_dbay(ip_address: str, ip_port: int):
    """Try to reach a DBay GUI server and return its loaded modules."""
    import json
    url = f"http://{ip_address}:{ip_port}/full-state"
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            state = json.loads(r.read())
        modules = [
            {"slot": m["core"]["slot"], "type": m["core"]["type"]}
            for m in state.get("data", [])
            if m.get("core", {}).get("type") not in ("empty", None)
        ]
        return {"reachable": True, "modules": modules}
    except Exception:
        return {"reachable": False, "modules": []}


class _SyncDbayBody(_BM):
    ip_address: str
    ip_port: int


@app.post("/api/manage-instruments/sync-dbay")
def api_sync_dbay(body: _SyncDbayBody, env: Env = Depends(get_env)):
    """Fetch modules from a running DBay GUI server and add them to the config."""
    import json
    from lab_wizard.lib.utilities.params_discovery import load_params_class

    config_dir = _config_dir(env)
    instruments = load_instruments(config_dir)

    dbay_params = next(
        (p for p in instruments.values()
         if getattr(p, "type", None) == "dbay"
         and getattr(p, "ip_address", None) == body.ip_address
         and getattr(p, "ip_port", None) == body.ip_port),
        None,
    )
    if dbay_params is None:
        raise HTTPException(status_code=404, detail="DBay not found in config")

    url = f"http://{body.ip_address}:{body.ip_port}/full-state"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            state = json.loads(r.read())
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Cannot reach DBay GUI server: {e}")

    _SYNC_TYPE_MAP = {"dac4D": "dac4D", "dac16D": "dac16D"}
    added = []
    for m in state.get("data", []):
        mtype = m.get("core", {}).get("type")
        slot = m.get("core", {}).get("slot")
        if mtype not in _SYNC_TYPE_MAP or slot is None:
            continue
        params_cls = load_params_class(_SYNC_TYPE_MAP[mtype])
        child_params = params_cls()
        child_params.slot = str(slot)
        child_key = instrument_hash(mtype, str(slot))
        dbay_params.children[child_key] = child_params
        added.append({"slot": slot, "type": mtype})

    save_instruments_to_config(instruments, config_dir)
    logger.info("DBay sync: added %d modules for %s:%s", len(added), body.ip_address, body.ip_port)
    return {"status": "ok", "added": added, "tree": get_configured_tree(config_dir)}


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


# Serve SvelteKit static build from resolved directory at root (mounted last)
app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="frontend")


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
    webview.start(storage_path=tempfile.mkdtemp(), debug=debug, icon="../static/icon.png")
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
        default=8884,
        help="Port to bind the server (default: 8884). Use 0 to auto-pick a free port.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_arguments()
    os.environ["LAB_WIZARD_LOG_LEVEL"] = "DEBUG" if args.debug else "INFO"
    configure_wizard_logging(logs_dir=Path(LOG_DIR), debug=args.debug)
    log_level = "debug" if args.debug else "info"

    server_ip = "0.0.0.0"
    webview_ip = "localhost"
    server_port = args.port  # allow override or auto-pick with 0
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
            print("Reachable URLs on this host:")
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