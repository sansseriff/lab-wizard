"""The wizard's FastAPI app: its startup, request logging, and routes.

The HTTP API itself lives in ``routes/``, one router per section of the GUI;
what every route needs to find its workspace is in ``deps.py``. The process
that serves this app, and the window that shows it, are in ``main.py``.
"""

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from lab_wizard.lib.data.network import warn_if_networked
from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata
from lab_wizard.wizard.backend.errors import install_error_handlers
from lab_wizard.wizard.backend.location import WEB_DIR
from lab_wizard.wizard.backend.logging_config import configure_wizard_logging
from lab_wizard.wizard.backend.models import Env
from lab_wizard.wizard.backend.server_control import (
    ensure_server,
    stop_managed_children,
)

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
    # the window's health poll (main._load_when_ready) therefore returns OK at exactly the
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
