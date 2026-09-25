"""Savers: optional extra outputs of a run, beside the lab database.

Every run of a project is recorded in the lab database whether or not it has a
saver (``lab_wizard.lib.data``). A saver is something more a project asks for,
such as a folder of CSV files for people who work with files.

A saver is a sink on the run's message stream, like the database recorder. It
sees ``RunStarted``, every ``Point``, every step's start and end, and
``RunEnded`` (``plans/semantic_data_plan.md`` §4), and handles them in
:meth:`GenericSaver.handle`. The database is the record, so a saver that fails
must never fail the run: :meth:`GenericSaver.attach` logs the error and stops
feeding that saver.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lab_procedure import MessageBus, Point, RunEnded, RunStarted, StepBegan, StepEnded

if TYPE_CHECKING:
    from lab_wizard.lib.data.recorder import DatabaseRecorder
    from lab_wizard.lib.savers.base import SaverParams

__all__ = ["GenericSaver", "SaverContext", "StandInSaver"]

logger = logging.getLogger(__name__)

RUN_MESSAGES = (RunStarted, Point, RunEnded)
STEP_MESSAGES = (StepBegan, StepEnded)


class SaverContext:
    """Where a run is happening, for a saver that needs to know.

    ``project_dir`` is the project's folder, or ``None`` for a step tree run on
    its own; ``recorder`` is what records the run in the lab database, or
    ``None``. The recorder handles each message before any saver does, so while
    a saver handles ``RunStarted`` the run already has its database id.
    """

    def __init__(self, project_dir: Path | None = None, recorder: "DatabaseRecorder | None" = None) -> None:
        self.project_dir = project_dir
        self.recorder = recorder

    @property
    def database(self) -> Path | None:
        return self.recorder.path if self.recorder is not None else None

    @property
    def run_id(self) -> int | None:
        return self.recorder.run_id if self.recorder is not None else None

    def device(self, name: str | None) -> dict[str, Any] | None:
        """The device's name and properties, as the lab database knows them."""
        if not name:
            return None
        if self.recorder is None:
            return {"name": name, "properties": {}}
        return self.recorder.device(name)


class GenericSaver(ABC):
    """Base class for savers: a sink on a run's messages."""

    def attach(self, data_bus: MessageBus, status_bus: MessageBus, context: SaverContext | None = None) -> None:
        """Start receiving a run's messages."""
        self.context = context or SaverContext()
        self._failed = False

        def guarded(message: object) -> None:
            if self._failed:
                return
            try:
                self.handle(message)
            except Exception:  # noqa: BLE001 - reported, and the run carries on
                self._failed = True
                logger.exception(
                    "%s failed and has stopped saving; the run continues and is still "
                    "recorded in the lab database",
                    type(self).__name__,
                )

        data_bus.subscribe(RUN_MESSAGES, guarded)
        status_bus.subscribe(STEP_MESSAGES, guarded)

    @abstractmethod
    def handle(self, message: Any) -> None:
        """Handle one ``RunStarted``, ``Point``, ``StepBegan``, ``StepEnded`` or ``RunEnded``."""

    @classmethod
    @abstractmethod
    def from_params(cls, params: "SaverParams") -> "GenericSaver":
        """Construct a runtime saver from its Params object."""

    @classmethod
    def from_config(cls, exp: Any, *, key: str) -> "GenericSaver":
        """Look up a saver Params on ``exp.savers`` by name and construct it.

        Uses ``params.create_inst()`` for polymorphic dispatch, so
        ``GenericSaver.from_config(...)`` gets the configured type.
        """
        params = exp.savers[key]
        return params.create_inst()


class StandInSaver(GenericSaver):
    """Keeps every message in memory. Useful for tests."""

    ignore_in_cli = True

    def __init__(self) -> None:
        self.messages: list[Any] = []

    def handle(self, message: Any) -> None:
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

    @classmethod
    def from_params(cls, params: "SaverParams") -> "StandInSaver":
        return cls()
