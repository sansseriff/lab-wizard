"""The live page on its own, for a run started from a terminal with ``live_plot: web``.

Started by :class:`~lab_wizard.lib.plotters.web_plotter.WebPlotter` as a
process of its own::

    python -m lab_wizard.wizard.backend.live_server --db data/lab.db --run 41 --port 8765 [--window]

It serves the wizard's ``/live`` page and the live websocket for one lab
database — nothing else of the wizard. With ``--window`` it opens the page in
a window (pywebview) and exits when the window closes. Without, it serves
until stopped, or until ``--linger`` seconds after the run ends.
"""

from __future__ import annotations

import argparse
import threading
import time
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from lab_wizard.lib.data.read import Lab
from lab_wizard.wizard.backend.location import WEB_DIR
from lab_wizard.wizard.backend.models import Env
from lab_wizard.wizard.backend.routes import live

__all__ = ["live_app", "main"]


def live_app(db: Path, config_dir: Path | None = None) -> FastAPI:
    """An app serving the live page and websocket for the lab database at ``db``."""
    app = FastAPI()
    # A run draws with the plots it recorded; config_dir only says whether its
    # procedure still exists, for the Data page's "save to procedure".
    app.state.env = Env(data_dir=db.parent, config_dir=config_dir or db.parent)
    app.include_router(live.router)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="frontend")
    return app


def _run_over(db: Path, run_id: int) -> bool:
    lab = Lab(db)
    try:
        rows = lab.query("SELECT status FROM runs WHERE id = ?", (run_id,))
        return bool(rows) and rows[0][0] != "running"
    finally:
        lab.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--run", required=True, type=int)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--config-dir", type=Path, default=None)
    parser.add_argument("--plot", default="")
    parser.add_argument("--window", action="store_true", help="open the page in a window; exit when it closes")
    parser.add_argument("--linger", type=float, default=600.0, help="without --window, seconds to keep serving after the run ends")
    args = parser.parse_args(argv)

    server = uvicorn.Server(
        uvicorn.Config(live_app(args.db, args.config_dir), host="127.0.0.1", port=args.port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)

    url = f"http://127.0.0.1:{args.port}/live/?run={args.run}" + (f"&plot={args.plot}" if args.plot else "")
    if args.window:
        import webview  # type: ignore

        webview.create_window(f"Run {args.run}", url, width=1100, height=760)
        webview.start()  # returns when the window is closed
    else:
        ended_at: float | None = None
        while thread.is_alive():
            if ended_at is None and _run_over(args.db, args.run):
                ended_at = time.monotonic()
            if ended_at is not None and time.monotonic() - ended_at > args.linger:
                break
            time.sleep(1.0)
    server.should_exit = True
    thread.join(timeout=5)


if __name__ == "__main__":
    main()
