from __future__ import annotations

import enum
import re
from typing import Self
from threading import Event

from lab_procedure.context import RunContext
from lab_procedure.messages import NodeId, StepBegan, StepEnded, StepProgress


_WORD_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def step_kind(cls: type) -> str:
    """``SourceGuard`` → ``source_guard``: the name a definition uses for the step."""
    return _WORD_BOUNDARY.sub("_", cls.__name__).lower()


class Status(enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    ABORTED = "aborted"


class Step:
    """A composable unit of a blocking lab procedure."""

    determinate = False

    def __init__(self, name: str | None = None) -> None:
        self.name = name or type(self).__name__
        # A step's segment in a path is the name its author gave it, or else
        # its kind — so paths read like the definition that built the tree.
        self.segment = name or step_kind(type(self))
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
        *,
        iteration: int | None = None,
    ) -> Status:
        """Run this step as a child of ``parent_id``.

        ``position`` is the child's place among its parent's children, shown as
        ``[n]``; ``iteration`` is which run of a repeated child this is (a
        sweep's value, a repeat's count, a retry's attempt), shown as ``#n``.
        The two are kept apart so a path read back later says which it was.
        """
        self.context = context
        self.parent_id = parent_id
        if iteration is not None:
            label = f"{self.segment}#{iteration}"
        elif position is not None:
            label = f"{self.segment}[{position}]"
        else:
            label = self.segment
        self.node_id = (parent_id or ()) + (label,)
        status = Status.ABORTED
        context.status_bus.emit(
            StepBegan(self.node_id, parent_id, self.name, self.determinate)
        )
        with context.executing(self.node_id):
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
