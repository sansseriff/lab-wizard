"""A matplotlib window, for a run started from a terminal at the lab computer.

It opens :mod:`~lab_wizard.lib.plotters.window` as a process of its own, which
follows the run in the lab database and stays open after it ends. With no
display (an SSH session without one), it says so once and draws nothing; the
run is unaffected.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from lab_wizard.lib.plotters.plotter import GenericPlotter, display_available

__all__ = ["MplPlotter"]

logger = logging.getLogger(__name__)


class MplPlotter(GenericPlotter):
    """Opens a matplotlib window on the run."""

    def __init__(self, plot: str = "") -> None:
        super().__init__(plot)
        self.window: subprocess.Popen[bytes] | None = None

    def command(self, database: Path, run_id: int) -> list[str]:
        command = [sys.executable, "-m", "lab_wizard.lib.plotters.window", "--db", str(database), "--run", str(run_id)]
        return command + (["--plot", self.plot_name] if self.plot_name else [])

    def run_started(self, database: Path, run_id: int) -> None:
        if not display_available():
            logger.warning(
                "outputs.live_plot is 'window', but there is no display here; no window "
                "opens. Use 'web' to follow a run over SSH."
            )
            return
        # Its own session, so a Ctrl-C meant for the run does not close it.
        self.window = subprocess.Popen(
            self.command(database, run_id),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            start_new_session=True,
        )
