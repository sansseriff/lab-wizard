from __future__ import annotations

from lab_procedure.messages import Point, RunEnded, RunStarted

from lab_wizard.lib.plotters.plotter import GenericPlotter


class PlotterSink:
    """Bridge lab_procedure messages to lab_wizard plotter instances.

    Mirrors :class:`~lab_wizard.lib.task_adapters.savers.SaverSink`: the
    ``data_bus`` is the pipe and ``Point.values`` is the wire format. Each
    point's values are forwarded to every plotter via the
    :meth:`GenericPlotter.plot` contract, so a local matplotlib plotter and a
    web Bokeh plotter consume the same stream without the sink knowing which.

    ``RunStarted``/``RunEnded`` are accepted (so this can subscribe to the same
    message tuple as ``SaverSink``) but are no-ops for now; a richer plotter
    lifecycle is deferred to a dedicated plotting design.
    """

    def __init__(self, plotters: list[GenericPlotter]) -> None:
        self.plotters = list(plotters)

    def handle(self, message: RunStarted | Point | RunEnded) -> None:
        if isinstance(message, Point):
            for plotter in self.plotters:
                plotter.plot(message.values)
