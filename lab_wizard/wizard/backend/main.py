"""The wizard's process: the FastAPI app, its startup, and the window it opens.

The HTTP API itself lives in ``routes/``, one router per section of the GUI;
what every route needs to find its workspace is in ``deps.py``.
"""

import asyncio
import logging
import multiprocessing
import multiprocessing.spawn
import os
import signal
import sys
import time
import urllib.request
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

try:
    import webview  # type: ignore
except Exception:  # ImportError or runtime issues shouldn't block headless mode
    webview = None  # type: ignore
import argparse
import tempfile
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any
from uuid import uuid4

from uvicorn import Config, Server

from lab_wizard.lib.data.network import warn_if_networked
from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata
from lab_wizard.wizard.backend.errors import install_error_handlers
from lab_wizard.wizard.backend.location import WEB_DIR
from lab_wizard.wizard.backend.logging_config import configure_wizard_logging
from lab_wizard.wizard.backend.macos_app import window_executable
from lab_wizard.wizard.backend.models import Env
from lab_wizard.wizard.backend.server_control import (
    ensure_server,
    stop_managed_children,
)
from lab_wizard.wizard.backend.utils_runtime import (
    get_ipv4_addresses,
    green,
    has_gui_context,
    is_ssh_session,
)

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
    if env.data_dir is not None:
        await asyncio.to_thread(warn_if_networked, env.data_dir)

    # Find-or-start this workspace's instrument server, so hardware has exactly
    # one owner and the wizard is a client of it. Off the event loop because
    # start_server waits briefly to confirm the child survived. Started
    # detached, so the daemon outlives this window and other workspaces on the
    # machine keep their instruments when the host's GUI is closed.
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


# Pass the lifespan manager to the FastAPI app
app = FastAPI(lifespan=lifespan)
install_error_handlers(app)


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


# ---- stopping cleanly ----
#
# The terminal sends Ctrl-C (SIGINT) to every process in the foreground group:
# the launcher, the server child and the window child alike. The launcher
# stops the other two in order; the server also shuts itself down gracefully
# on hearing it, and the window ignores it and waits to be closed.

_STOP_SIGNALS = ("SIGINT", "SIGTERM", "SIGHUP")


class _StopRequested(Exception):
    """A signal asked the wizard to stop."""


def _raise_stop(signum: int, _frame: Any) -> None:
    raise _StopRequested(signal.Signals(signum).name)


def _set_signals(names: tuple[str, ...], handler: Any) -> None:
    for name in names:
        sig = getattr(signal, name, None)  # SIGHUP does not exist on Windows
        if sig is not None:
            signal.signal(sig, handler)


class UvicornServer(multiprocessing.Process):
    def __init__(self, config: Config):
        super().__init__()
        self.server = Server(config=config)
        self.config = config

    def stop(self, timeout: float = 15.0):
        """SIGTERM is uvicorn's graceful path: in-flight requests finish and the
        lifespan shutdown (stopping managed children) runs. Killed only if it
        will not go."""
        if self.is_alive():
            self.terminate()
            self.join(timeout)
        if self.is_alive():
            logger.warning("Wizard server did not stop within %.0fs; killing it", timeout)
            self.kill()
            self.join()

    def run(self):
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
        "workspace": str(getattr(env, "workspace_dir", "") or ""),
        "pid": os.getpid(),
    }


from lab_wizard.wizard.backend.routes import ROUTERS  # noqa: E402

for _router in ROUTERS:
    app.include_router(_router)

app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="frontend")


def _port_is_free(port: int, host: str = "0.0.0.0") -> bool:
    """Whether the server could bind ``port``, tested the way it will bind it.

    Deliberately mirrors the real bind: same host, and **no SO_REUSEADDR**. On
    BSD/macOS that option lets a probe bind 127.0.0.1 while another process
    holds 0.0.0.0 on the same port, so the probe reports free and the server
    then fails to start — the exact collision this is meant to prevent.
    """
    import socket as _socket

    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
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
        s.bind(("0.0.0.0", 0))
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
    # The launcher decides when the window goes; a Ctrl-C here would only
    # print a traceback.
    _set_signals(("SIGINT",), signal.SIG_IGN)

    health_url = url_to_load.rstrip("/") + "/api/health"
    _wait_for_server(health_url)

    def on_closed():
        pipe_send.send("closed")

    # The Data page's "Export run" is a download; without this the window
    # silently drops it, where a browser would ask where to save.
    webview.settings["ALLOW_DOWNLOADS"] = True

    _win: Any = webview.create_window(  # type: ignore
        "Lab Wizard",
        url=url_to_load,
        resizable=True,
        width=1300,
        height=900,
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
    windowsp: multiprocessing.Process | None = None
    _set_signals(_STOP_SIGNALS, _raise_stop)

    try:
        # If port 0 (auto), we can't easily query the bound port from uvicorn.Server in this process
        # without IPC, so keep to explicit ports for now. If needed, add a pipe to report.
        url = f"http://{webview_ip}:{server_port}/"
        should_spawn_ui = (not args.no_ui) and has_gui_context()

        # Refuse to show a window until the server answering is demonstrably ours.
        _verify_own_server(server_port, str(runtime_env.workspace_dir or ""))
        logger.info(
            "Wizard serving %s on port %d", runtime_env.workspace_dir, server_port
        )

        if should_spawn_ui:
            # Then start window
            windowsp = multiprocessing.Process(
                target=start_window,
                args=(conn_send, url, args.debug),
            )

            # On macOS the window runs from Lab Wizard.app so the Dock shows
            # its icon. The executable is process-wide, so only for this start.
            bundled = window_executable()
            default_executable = multiprocessing.spawn.get_executable()
            if bundled:
                multiprocessing.set_executable(bundled)
            try:
                windowsp.start()
            finally:
                multiprocessing.set_executable(default_executable)

            window_status = ""
            while "closed" not in window_status:
                window_status = conn_recv.recv()
                logger.debug("Window status event: %s", window_status)
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
            instance.join()
    except _StopRequested as stop:
        print(f"\n{stop} received; stopping the wizard…", flush=True)
    finally:
        # Once stopping, stay stopping: a second Ctrl-C must not interrupt
        # the shutdown halfway.
        _set_signals(_STOP_SIGNALS, signal.SIG_IGN)
        if windowsp is not None and windowsp.is_alive():
            windowsp.terminate()
            windowsp.join(5)
        instance.stop()
        logger.info("Wizard stopped")
