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
    """Run a child ``count`` times, binding ``parameter`` to 0, 1, 2 ...

    The binding is what keeps repetitions apart in the data: ten counts at one
    bias are ten rows told apart by ``repeat``, exactly as a sweep's rows are
    told apart by its value.
    """

    determinate = True

    def __init__(
        self,
        count: int,
        child_factory: Callable[[int], Step] | Step,
        name: str | None = None,
        *,
        parameter: str = "repeat",
    ) -> None:
        super().__init__(name=name)
        if count < 0:
            raise ValueError("Repeat count must be non-negative")
        self.count = count
        self.child_factory = child_factory
        self.parameter = parameter
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
        assert self.context is not None
        for index in range(self.count):
            if self.aborted:
                return Status.ABORTED
            self.report_progress(
                index / self.count,
                detail=f"repeat {index + 1}/{self.count}",
            )
            with self.context.bound_parameter(self.parameter, index):
                child = self._make_child(index)
                self._active_child = child
                try:
                    status = child.execute(self.context, self.node_id, iteration=index)
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
                    status = child.execute(self.context, self.node_id, iteration=index)
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


class WithParameter(Step):
    """Run ``body`` with ``parameter`` set to ``value``, as a sweep would.

    Every observation recorded inside carries it, so a run can label its rows —
    ``phase: background`` for a dark count, ``phase: signal`` for the sweep —
    without a sweep of one value.
    """

    def __init__(self, parameter: str, value: object, body: Step, name: str | None = None) -> None:
        super().__init__(name=name)
        self.parameter = parameter
        self.value = value
        self.body = body
        self.add_child(body)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        with self.context.bound_parameter(self.parameter, self.value):
            return self.body.execute(self.context, self.node_id, position=0)


# --------------------------------------------------------------------------
# Control flow
#
# A procedure is a behavior tree: every step returns SUCCESS, FAILED or
# ABORTED, so branching needs no expression language. A condition is a leaf
# that returns FAILED instead of SUCCESS, and these composites decide what a
# failure means. ABORTED always propagates at once — an operator stopping a run
# must never be mistaken for a step that merely failed.
# --------------------------------------------------------------------------


class Retry(Step):
    """Run ``child`` until it succeeds, at most ``max_attempts`` times.

    A child that returns FAILED *or raises* is tried again — a counter timeout
    is exactly the failure worth retrying. After the last attempt the final
    exception is re-raised, or FAILED returned.
    """

    determinate = False

    def __init__(self, max_attempts: int, child: Step, name: str | None = None) -> None:
        super().__init__(name=name)
        if max_attempts < 1:
            raise ValueError("Retry max_attempts must be at least 1")
        self.max_attempts = max_attempts
        self.child = child
        self.add_child(child)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        for attempt in range(self.max_attempts):
            if self.aborted:
                return Status.ABORTED
            last_attempt = attempt == self.max_attempts - 1
            try:
                status = self.child.execute(self.context, self.node_id, iteration=attempt)
            except Exception:
                if last_attempt:
                    raise
                continue
            if status is not Status.FAILED:
                return status  # SUCCESS, or ABORTED propagating
        return Status.FAILED


class If(Step):
    """Run ``condition``; on SUCCESS run ``then``, on FAILED run ``otherwise``.

    With no ``otherwise``, a failed condition simply skips ``then`` and the
    step succeeds. The result is the status of whichever branch ran.
    """

    def __init__(
        self,
        condition: Step,
        then: Step,
        otherwise: Step | None = None,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.condition = condition
        self.then = then
        self.otherwise = otherwise
        self.add_child(condition)
        self.add_child(then)
        if otherwise is not None:
            self.add_child(otherwise)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        verdict = self.condition.execute(self.context, self.node_id, position=0)
        if verdict is Status.ABORTED:
            return verdict
        if verdict is Status.SUCCESS:
            return self.then.execute(self.context, self.node_id, position=1)
        if self.otherwise is None:
            return Status.SUCCESS
        return self.otherwise.execute(self.context, self.node_id, position=2)


class Selector(Step):
    """Try each child in order; succeed with the first that succeeds.

    A fallback: ``Selector(fast_path, slow_path)``. FAILED only if every child
    fails.
    """

    def __init__(self, *children: Step, name: str | None = None) -> None:
        super().__init__(name=name)
        for child in children:
            self.add_child(child)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        for index, child in enumerate(self.children):
            if self.aborted:
                return Status.ABORTED
            status = child.execute(self.context, self.node_id, position=index)
            if status is not Status.FAILED:
                return status
        return Status.FAILED


class Invert(Step):
    """Succeed when ``child`` fails, fail when it succeeds. ABORTED passes through.

    Turns a condition around: ``If(Invert(ValueAbove(...)), ...)``.
    """

    def __init__(self, child: Step, name: str | None = None) -> None:
        super().__init__(name=name)
        self.child = child
        self.add_child(child)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        status = self.child.execute(self.context, self.node_id, position=0)
        if status is Status.SUCCESS:
            return Status.FAILED
        if status is Status.FAILED:
            return Status.SUCCESS
        return status


class _Comparison(Step):
    """Compare the latest recorded ``field`` against ``threshold``."""

    def __init__(self, field: str, threshold: float, name: str | None = None) -> None:
        super().__init__(name=name)
        self.field = field
        self.threshold = threshold

    def _latest(self) -> float:
        assert self.context is not None
        if self.field not in self.context.latest:
            # A condition on a value this run never recorded is a mistake in
            # the procedure, not a measurement outcome, so it is not FAILED.
            recorded = ", ".join(sorted(self.context.latest)) or "nothing yet"
            raise KeyError(
                f"{type(self).__name__} reads {self.field!r}, but this run has "
                f"recorded no such value (recorded: {recorded})"
            )
        return float(self.context.latest[self.field])


class ValueAbove(_Comparison):
    """SUCCESS if the latest ``field`` is above ``threshold``, else FAILED."""

    def run(self) -> Status:
        return Status.SUCCESS if self._latest() > self.threshold else Status.FAILED


class ValueBelow(_Comparison):
    """SUCCESS if the latest ``field`` is below ``threshold``, else FAILED."""

    def run(self) -> Status:
        return Status.SUCCESS if self._latest() < self.threshold else Status.FAILED
