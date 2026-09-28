from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lab_procedure import MessageBus
from lab_procedure.messages import RunEnded, RunStarted

from lab_wizard.lib.plotters.plotter import GenericPlotter

if TYPE_CHECKING:
    from lab_wizard.lib.data.recorder import DatabaseRecorder

logger = logging.getLogger(__name__)


class PlotterSink:
    """Tell plotters which run to show, once the recorder has given it an id.

    A plotter is a viewer of the lab database (``lib/plotters/plotter.py``),
    so all it needs from the run is where it is recorded. It is subscribed
    after the recorder, which handles ``RunStarted`` first and so has the id.
    A plotter that fails is logged and the run carries on: a plot is never
    worth a measurement.
    """

    def __init__(self, plotters: list[GenericPlotter], recorder: DatabaseRecorder | None) -> None:
        self.plotters = list(plotters)
        self.recorder = recorder

    def attach(self, data_bus: MessageBus) -> None:
        data_bus.subscribe((RunStarted, RunEnded), self.handle)

    def handle(self, message: RunStarted | RunEnded) -> None:
        if not self.plotters:
            return
        if isinstance(message, RunStarted) and (self.recorder is None or self.recorder.run_id is None):
            logger.warning("A live plot needs the run recorded in a lab database; this run is not, so none opens")
            return
        for plotter in self.plotters:
            try:
                if isinstance(message, RunStarted):
                    assert self.recorder is not None and self.recorder.run_id is not None
                    plotter.run_started(self.recorder.path, self.recorder.run_id)
                else:
                    plotter.run_ended(message.status)
            except Exception:  # noqa: BLE001 - reported, and the run carries on
                logger.exception("%s failed; the run continues", type(plotter).__name__)
