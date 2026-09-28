"""The live web page, for a run started from a terminal.

It starts the live server (``lab_wizard.wizard.backend.live_server``) as a
process of its own, serving the wizard's ``/live`` page — the run's plots and
its timeline, not the rest of the wizard — for this run. Then:

* **here**, with a display: the page opens in a window, which stays until it
  is closed;
* **over SSH**: it prints the link, and the ``ssh -L`` command that reaches it
  from your own computer. When the run ends, the page stays up until Enter.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path

from lab_wizard.lib.plotters.plotter import GenericPlotter, display_available
from lab_wizard.lib.workspace import find_workspace

__all__ = ["WebPlotter"]

logger = logging.getLogger(__name__)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ssh_host() -> str:
    """This machine as the SSH client reached it, for the tunnel command."""
    parts = os.environ.get("SSH_CONNECTION", "").split()
    return parts[2] if len(parts) >= 3 else "<this-machine>"


class WebPlotter(GenericPlotter):
    """Serves the live page for the run, in a window here or as a link over SSH."""

    def __init__(self, plot: str = "") -> None:
        super().__init__(plot)
        self.server: subprocess.Popen[bytes] | None = None
        self.windowed = False

    def command(self, database: Path, run_id: int, port: int, window: bool) -> list[str]:
        command = [
            sys.executable, "-m", "lab_wizard.wizard.backend.live_server",
            "--db", str(database), "--run", str(run_id), "--port", str(port),
        ]
        workspace = find_workspace(database, use_environment=False)
        if workspace is not None:
            command += ["--config-dir", str(workspace.config_dir)]
        if self.plot_name:
            command += ["--plot", self.plot_name]
        return command + (["--window"] if window else [])

    def run_started(self, database: Path, run_id: int) -> None:
        port = _free_port()
        self.windowed = display_available()
        self.server = subprocess.Popen(
            self.command(database, run_id, port, self.windowed),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,  # a Ctrl-C meant for the run leaves the page up
        )
        if not self.windowed:
            print(
                f"\nLive plot: http://127.0.0.1:{port}/live/?run={run_id}\n"
                f"From your own computer, first run:  ssh -N -L {port}:127.0.0.1:{port} {_ssh_host()}\n",
                flush=True,
            )

    def finish(self) -> None:
        """Over SSH, keep the page up until Enter; a window looks after itself."""
        if self.server is None or self.windowed or self.server.poll() is not None:
            return
        if sys.stdin is not None and sys.stdin.isatty():
            try:
                input("The run is over; the live plot is still up. Press Enter to stop it. ")
            except (EOFError, KeyboardInterrupt):
                pass
            self.server.terminate()
        # Not interactive: the server stops on its own, ten minutes after the run.
