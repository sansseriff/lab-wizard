"""The wizard's process: it serves the app (``app.py``) and opens its window.

Three processes: this launcher, the server child, and the window child. The
launcher imports only what launching needs — the app, with FastAPI, polars and
every route, is imported by the server child alone — so the window can start
alongside the server and show a loading page within a fraction of a second.
"""

import argparse
import json
import logging
import multiprocessing
import multiprocessing.spawn
import os
import signal
import sys
import tempfile
import time
import urllib.request
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from uvicorn import Config, Server

from lab_wizard.lib.workspace import require_workspace
from lab_wizard.wizard.backend.location import WEB_DIR
from lab_wizard.wizard.backend.logging_config import configure_wizard_logging
from lab_wizard.wizard.backend.macos_app import window_executable
from lab_wizard.wizard.backend.utils_runtime import (
    get_ipv4_addresses,
    green,
    has_gui_context,
    is_ssh_session,
)

FRAMELESS = False
ICON_PATH = Path(WEB_DIR) / "icon.png"
LOADING_PAGE = Path(__file__).with_name("loading.html")
logger = logging.getLogger("lab_wizard.wizard.backend.main")


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
    while time.monotonic() < deadline:
        serving = _serving_workspace(port)
        if serving is None:
            time.sleep(0.05)
        elif not workspace or serving == workspace:
            return
        else:
            raise SystemExit(
                f"Port {port} is served by a different Lab Wizard "
                f"(workspace {serving!r}), not this one "
                f"({workspace!r}). This workspace's server failed to "
                "start — check the log above for the reason."
            )
    raise SystemExit(
        f"This workspace's wizard did not come up on port {port} within "
        f"{timeout:.0f}s. Check the log above for the reason."
    )


def _serving_workspace(port: int) -> str | None:
    """The workspace of the wizard answering on ``port``, or None if none is yet."""
    try:
        with urllib.request.urlopen(
            f"http://localhost:{port}/api/health", timeout=1
        ) as r:
            if r.status == 200:
                return str(json.loads(r.read().decode()).get("workspace", ""))
    except Exception:
        pass
    return None


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


def _load_when_ready(window: Any, url: str, port: int, workspace: str) -> None:
    """Swap the loading page for the app once this workspace's server answers.

    Only *our* server: should another wizard hold the port, the launcher reports
    it and closes this window, which keeps the loading page until then.
    """
    while True:
        serving = _serving_workspace(port)
        if serving is not None and (not workspace or serving == workspace):
            break
        time.sleep(0.05)
    window.load_url(url)


def start_window(
    pipe_send: Connection, url_to_load: str, port: int, workspace: str, debug: bool = False
):
    """Open the window at once on a loading page; the app replaces it when ready."""
    try:
        import webview  # type: ignore
    except Exception as exc:
        raise RuntimeError("pywebview is not available; cannot start UI window") from exc
    # The launcher decides when the window goes; a Ctrl-C here would only
    # print a traceback.
    _set_signals(("SIGINT",), signal.SIG_IGN)

    def on_closed():
        pipe_send.send("closed")

    # The Data page's "Export run" is a download; without this the window
    # silently drops it, where a browser would ask where to save.
    webview.settings["ALLOW_DOWNLOADS"] = True

    _win: Any = webview.create_window(  # type: ignore
        "Lab Wizard",
        html=LOADING_PAGE.read_text(encoding="utf-8"),
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
            _load_when_ready,
            (_win, url_to_load, port, workspace),
            storage_path=storage_dir,
            debug=debug,
            icon=icon_path,
            # Linux installs only the Qt backend, but pywebview tries GTK first
            # and logs a traceback when it is missing. PYWEBVIEW_GUI still wins.
            gui="qt" if sys.platform == "linux" and "PYWEBVIEW_GUI" not in os.environ else None,
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
    workspace = require_workspace()
    configure_wizard_logging(logs_dir=workspace.logs_dir, debug=args.debug)
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
    app_import_str = "lab_wizard.wizard.backend.app:app"
    # Per-request access lines only in --debug: loading the UI alone fetches
    # dozens of assets, which buried the startup messages and any errors.
    config = Config(
        app_import_str,
        host=server_ip,
        port=server_port,
        log_level=log_level,
        workers=1,
        access_log=args.debug,
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

        if should_spawn_ui:
            # Alongside the server, not after it: the window shows a loading
            # page straight away and loads the app once the server is ours.
            windowsp = multiprocessing.Process(
                target=start_window,
                args=(conn_send, url, server_port, str(workspace.root), args.debug),
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

        # The window will not show the app until the server answering is
        # demonstrably ours; if it is not, this stops with the reason.
        _verify_own_server(server_port, str(workspace.root))
        logger.info("Wizard serving %s on port %d", workspace.root, server_port)

        if windowsp is not None:
            window_status = ""
            while "closed" not in window_status:
                window_status = conn_recv.recv()
                logger.debug("Window status event: %s", window_status)
        else:
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
