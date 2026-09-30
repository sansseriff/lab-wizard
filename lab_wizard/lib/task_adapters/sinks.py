"""Everything that consumes a run: the lab database first, then every sink.

A run's messages — ``RunStarted``, each ``Point``, each step's start and end,
``RunEnded`` (``plans/semantic_data_plan.md`` §4) — go to one
:class:`RunOutputs`, which hands each message to the database recorder first
and then to every :class:`RunSink` in turn, with :class:`RunInfo` saying where
the run is recorded. So a sink that needs the run's database id has it by the
time it sees ``RunStarted``: that order is what ``RunOutputs`` does, not an
accident of who subscribed first.

A sink is anything more a run produces: a folder of files
(:class:`~lab_wizard.lib.savers.FileSaver`), a live plot
(:class:`~lab_wizard.lib.plotters.GenericPlotter`), or one of your own. The
database is the record, so a sink that fails is logged and stops being fed,
and the run carries on; the recorder failing fails the run.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from lab_procedure import MessageBus, Point, RunEnded, RunStarted, StepBegan, StepEnded

from lab_wizard.lib.data.recorder import DatabaseRecorder

__all__ = ["RunInfo", "RunOutputs", "RunSink", "StandInSink"]

logger = logging.getLogger(__name__)

RUN_MESSAGES = (RunStarted, Point, RunEnded)
STEP_MESSAGES = (StepBegan, StepEnded)


class RunInfo:
    """Where the run a sink is watching happens, and where it is recorded.

    ``project_dir`` is the project's folder, or ``None`` for a run outside a
    project. The rest is ``None`` for a run nobody records.
    """

    def __init__(self, project_dir: Path | None = None, recorder: DatabaseRecorder | None = None) -> None:
        self.project_dir = project_dir
        self._recorder = recorder

    @property
    def database(self) -> Path | None:
        return self._recorder.path if self._recorder is not None else None

    @property
    def run_id(self) -> int | None:
        """The run's id in the lab database, from its ``RunStarted`` on."""
        return self._recorder.run_id if self._recorder is not None else None

    def device(self, name: str | None) -> dict[str, Any] | None:
        """The device's name and properties, as the lab database knows them."""
        if not name:
            return None
        if self._recorder is None:
            return {"name": name, "properties": {}}
        return self._recorder.device(name)


class RunSink(ABC):
    """Something a run produces besides its database record."""

    @abstractmethod
    def handle(self, message: Any, run: RunInfo) -> None:
        """One ``RunStarted``, ``Point``, ``StepBegan``, ``StepEnded`` or ``RunEnded``."""

    def finish(self) -> None:
        """Called once the run has returned. A live view may wait here until it is closed."""


class StandInSink(RunSink):
    """Keeps every message in memory. Useful for tests."""

    def __init__(self) -> None:
        self.messages: list[Any] = []
        self.run: RunInfo | None = None

    def handle(self, message: Any, run: RunInfo) -> None:
        self.run = run
        self.messages.append(message)

    @property
    def run_started(self) -> RunStarted | None:
        return next((m for m in self.messages if isinstance(m, RunStarted)), None)

    @property
    def run_ended(self) -> RunEnded | None:
        return next((m for m in self.messages if isinstance(m, RunEnded)), None)

    @property
    def points(self) -> list[Point]:
        return [m for m in self.messages if isinstance(m, Point)]


class RunOutputs:
    """Feeds a run's messages to its recorder, then to each sink, in order."""

    def __init__(
        self,
        recorder: DatabaseRecorder | None = None,
        sinks: list[RunSink] | None = None,
        *,
        project_dir: Path | None = None,
    ) -> None:
        self.recorder = recorder
        self.sinks = list(sinks or [])
        self.run = RunInfo(project_dir, recorder)
        self._failed: set[int] = set()

    def attach(self, data_bus: MessageBus, status_bus: MessageBus) -> None:
        """Start receiving a run's messages from its two buses."""
        data_bus.subscribe(RUN_MESSAGES, self.handle)
        status_bus.subscribe(STEP_MESSAGES, self.handle)

    def handle(self, message: Any) -> None:
        if self.recorder is not None:
            self.recorder.handle(message)
        for sink in self.sinks:
            if id(sink) in self._failed:
                continue
            try:
                sink.handle(message, self.run)
            except Exception:  # noqa: BLE001 - reported, and the run carries on
                self._failed.add(id(sink))
                logger.exception(
                    "%s failed and has stopped; the run continues and is still recorded in the lab database",
                    type(sink).__name__,
                )

    def close(self) -> None:
        """The run has returned: close the database, and let each sink finish."""
        if self.recorder is not None:
            self.recorder.close()
        for sink in self.sinks:
            try:
                sink.finish()
            except Exception:  # noqa: BLE001 - the run is over and recorded either way
                logger.exception("%s failed while finishing", type(sink).__name__)
