"""Live views of a running run: one websocket per run, fed from the lab database.

The run process never talks to a page. It records into the lab database; this
route watches the run there (:class:`~lab_wizard.wizard.backend.live.LiveFeed`)
and pushes what changed. So a page can connect before, during or after a run,
the wizard can restart mid-run and pick up where it was, and the standalone
live page a web plotter opens is this same route in a smaller app.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from lab_wizard.lib.data.schema import DATABASE_NAME
from lab_wizard.wizard.backend.live import LiveFeed
from lab_wizard.wizard.backend.models import Env

logger = logging.getLogger("lab_wizard.wizard.backend.routes.live")
router = APIRouter()

POLL_S = 0.25


def _env(websocket: WebSocket) -> Env:
    env = getattr(websocket.app.state, "env", None)
    if env is None:
        env = Env.from_current_workspace()
        websocket.app.state.env = env
    return env


@router.websocket("/api/live/runs/{run_id}")
async def live_run(websocket: WebSocket, run_id: int):
    """Everything about run ``run_id`` as it happens; see ``live.py`` for the messages."""
    await websocket.accept()
    env = _env(websocket)
    if env.data_dir is None:
        await websocket.close(code=1011, reason="no lab database")
        return
    feed = LiveFeed(env.data_dir / DATABASE_NAME, env.config_dir or env.data_dir, run_id)
    try:
        while True:
            try:
                messages = await asyncio.to_thread(feed.poll)
            except KeyError:
                await websocket.send_json({"type": "error", "message": f"No run {run_id} in this lab database"})
                break
            for message in messages:
                await websocket.send_json(message)
            if feed.ended:
                break
            await asyncio.sleep(POLL_S)
        await websocket.close()
    except WebSocketDisconnect:
        pass
