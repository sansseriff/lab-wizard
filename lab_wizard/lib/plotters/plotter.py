"""Plotters: draw a run while it is going, when it is started from a terminal.

A project picks one with ``outputs.live_plot`` (``window`` or ``web``), and
``outputs.plot`` names which of the procedure's ``plots:`` it opens on (empty:
the first). A run the wizard launches is drawn on its Run page instead, so it
gets no plotter.

Every plotter is a *viewer of the lab database*. The run already writes each
point and each step there as it happens, so a plotter only needs to know which
run to show: it opens its viewer, in a process of its own, when the run
starts. That keeps drawing out of the process driving the instruments — a slow
redraw can never delay a measurement, and a closed window never stops one —
and it means the window, the web page and the wizard's Run page all show the
same rows, computed the same way (``plans/runner_plan.md`` §7).
"""

from __future__ import annotations

import logging
import os
import sys
from abc import abstractmethod
from pathlib import Path
from typing import Any

from lab_procedure import RunEnded, RunStarted

from lab_wizard.lib.task_adapters.sinks import RunInfo, RunSink
from lab_wizard.lib.utilities.ssh import is_ssh_session

__all__ = ["GenericPlotter", "StandInPlotter", "display_available"]

logger = logging.getLogger(__name__)


def display_available() -> bool:
    """Whether a window opened here would be seen by the person running this.

    On Linux a display (X forwarding included) is enough; on macOS and Windows
    a session over SSH has no screen of its own.
    """
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return not is_ssh_session()


class GenericPlotter(RunSink):
    """Base class for plotters: told when a run starts, ends, and returns."""

    def __init__(self, plot: str = "") -> None:
        # A name from the procedure's plots:, or "" for the first.
        self.plot_name = plot

    def handle(self, message: Any, run: RunInfo) -> None:
        if isinstance(message, RunStarted):
            if run.database is None or run.run_id is None:
                logger.warning("A live plot needs the run recorded in a lab database; this run is not, so none opens")
                return
            self.run_started(run.database, run.run_id)
        elif isinstance(message, RunEnded):
            self.run_ended(message.status)

    @abstractmethod
    def run_started(self, database: Path, run_id: int) -> None:
        """The run is recorded as ``run_id`` in ``database``: open a view of it."""

    def run_ended(self, status: str) -> None:
        """The run ended with ``status``."""

class StandInPlotter(GenericPlotter):
    """Remembers what it was told. Useful for tests."""

    def __init__(self, plot: str = "") -> None:
        super().__init__(plot)
        self.started: tuple[Path, int] | None = None
        self.ended: str | None = None
        self.finished = False

    def run_started(self, database: Path, run_id: int) -> None:
        self.started = (database, run_id)

    def run_ended(self, status: str) -> None:
        self.ended = status

    def finish(self) -> None:
        self.finished = True
