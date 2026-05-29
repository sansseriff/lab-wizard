from __future__ import annotations

import enum
from typing import Self
from threading import Event

from lab_procedure.context import RunContext
from lab_procedure.messages import NodeId, StepBegan, StepEnded, StepProgress


class Status(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ABORTED = "aborted"


class Step:
    """A composable unit of a blocking lab procedure."""

    determinate = False

    def __init__(self, name: str | None = None) -> None:
        self.name = name or type(self).__name__
        self.children: list[Step] = []
        self.context: RunContext | None = None
        self.node_id: NodeId | None = None
        self.parent_id: NodeId | None = None
        self._abort_event = Event()

    def add_child(self, step: Step) -> Self:
        self.children.append(step)
        return self

    def on_enter(self) -> None:
        pass

    def run(self) -> Status:
        raise NotImplementedError

    def on_exit(self, status: Status) -> None:
        pass

    @property
    def aborted(self) -> bool:
        return self._abort_event.is_set()

    def abort(self) -> None:
        self._abort_event.set()
        for child in self.children:
            child.abort()

    def sleep(self, seconds: float) -> bool:
        return not self._abort_event.wait(timeout=max(seconds, 0.0))

    def report_progress(self, fraction: float, detail: str | None = None) -> None:
        if self.context is None or self.node_id is None:
            return
        bounded = min(max(fraction, 0.0), 1.0)
        self.context.status_bus.emit(StepProgress(self.node_id, bounded, detail))

    def execute(
        self,
        context: RunContext,
        parent_id: NodeId | None = None,
        position: int | None = None,
    ) -> Status:
        self.context = context
        self.parent_id = parent_id
        label = self.name if position is None else f"{self.name}[{position}]"
        self.node_id = (parent_id or ()) + (label,)
        status = Status.ABORTED
        context.status_bus.emit(
            StepBegan(self.node_id, parent_id, self.name, self.determinate)
        )
        try:
            self.on_enter()
            if self.aborted:
                return Status.ABORTED
            status = self.run()
            return status
        except Exception:
            status = Status.FAILED
            raise
        finally:
            try:
                self.on_exit(status)
            finally:
                context.status_bus.emit(StepEnded(self.node_id, status.value))
