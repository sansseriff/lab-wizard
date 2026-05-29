from __future__ import annotations

import time
from collections.abc import Callable, Iterable

from lab_procedure.core import Status, Step


class Sequence(Step):
    determinate = True

    def __init__(self, *children: Step, name: str | None = None) -> None:
        super().__init__(name=name)
        for child in children:
            self.add_child(child)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        total = len(self.children)
        if total == 0:
            self.report_progress(1.0)
            return Status.SUCCESS
        for index, child in enumerate(self.children):
            if self.aborted:
                return Status.ABORTED
            self.report_progress(index / total, detail=f"step {index + 1}/{total}")
            status = child.execute(self.context, self.node_id, position=index)
            if status is not Status.SUCCESS:
                return status
        self.report_progress(1.0)
        return Status.SUCCESS


class Repeat(Step):
    determinate = True

    def __init__(
        self,
        count: int,
        child_factory: Callable[[int], Step] | Step,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        if count < 0:
            raise ValueError("Repeat count must be non-negative")
        self.count = count
        self.child_factory = child_factory
        self._active_child: Step | None = None

    def abort(self) -> None:
        super().abort()
        if self._active_child is not None:
            self._active_child.abort()

    def _make_child(self, index: int) -> Step:
        if isinstance(self.child_factory, Step):
            return self.child_factory
        return self.child_factory(index)

    def run(self) -> Status:
        assert self.node_id is not None
        if self.count == 0:
            self.report_progress(1.0)
            return Status.SUCCESS
        for index in range(self.count):
            if self.aborted:
                return Status.ABORTED
            assert self.context is not None
            self.context.sweep_index = index
            self.report_progress(
                index / self.count,
                detail=f"repeat {index + 1}/{self.count}",
            )
            child = self._make_child(index)
            self._active_child = child
            try:
                status = child.execute(self.context, self.node_id, position=index)
            finally:
                self._active_child = None
            if status is not Status.SUCCESS:
                return status
        self.report_progress(1.0)
        return Status.SUCCESS


class Sweep(Step):
    determinate = True

    def __init__(
        self,
        parameter: str,
        values: Iterable[object],
        child_factory: Callable[[object], Step],
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.parameter = parameter
        self.values = list(values)
        self.child_factory = child_factory
        self._active_child: Step | None = None

    def abort(self) -> None:
        super().abort()
        if self._active_child is not None:
            self._active_child.abort()

    def run(self) -> Status:
        assert self.node_id is not None
        total = len(self.values)
        if total == 0:
            self.report_progress(1.0)
            return Status.SUCCESS
        assert self.context is not None
        for index, value in enumerate(self.values):
            if self.aborted:
                return Status.ABORTED
            self.report_progress(index / total, detail=f"{self.parameter}={value}")
            with self.context.bound_parameter(self.parameter, value):
                child = self.child_factory(value)
                self._active_child = child
                try:
                    status = child.execute(self.context, self.node_id, position=index)
                finally:
                    self._active_child = None
            if status is not Status.SUCCESS:
                return status
        self.report_progress(1.0)
        return Status.SUCCESS


class Wait(Step):
    determinate = True

    def __init__(
        self,
        seconds: float,
        *,
        progress_interval: float = 0.25,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        if seconds < 0:
            raise ValueError("Wait seconds must be non-negative")
        if progress_interval <= 0:
            raise ValueError("Wait progress_interval must be positive")
        self.seconds = seconds
        self.progress_interval = progress_interval
        self._t0 = 0.0

    def on_enter(self) -> None:
        self._t0 = time.monotonic()

    def run(self) -> Status:
        if self.seconds == 0:
            self.report_progress(1.0)
            return Status.SUCCESS
        while True:
            elapsed = time.monotonic() - self._t0
            if elapsed >= self.seconds:
                self.report_progress(1.0)
                return Status.SUCCESS
            if self.aborted:
                return Status.ABORTED
            self.report_progress(elapsed / self.seconds)
            remaining = self.seconds - elapsed
            if not self.sleep(min(self.progress_interval, remaining)):
                return Status.ABORTED
