from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping, Sequence as Seq
from contextlib import ExitStack
from typing import Literal

from pydantic import Field

from lab_procedure.core import Status, Step
from lab_procedure.schema import AnyStep, BuildContext, ParamRef, RenderContext, StepClass, StepParams, Value


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


class SequenceStepParams(StepParams):
    """Run steps in order; stop at the first that does not succeed."""

    type: Literal["sequence"] = "sequence"
    children: list[AnyStep] = Field(default_factory=list)

    @classmethod
    def step_class(cls) -> StepClass:
        return Sequence


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


class RepeatStepParams(StepParams):
    """Run ``body`` ``count`` times; its rows carry ``parameter`` = 0, 1, 2 ...

    The index keeps repetitions apart in the data, the way a sweep's value
    keeps its points apart.
    """

    type: Literal["repeat"] = "repeat"
    count: Value
    parameter: str = Field(default="repeat", json_schema_extra={"column": "records"})
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Repeat

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)

    # Repeat's constructor calls its child argument ``child_factory``, and a
    # step instance is accepted there; a definition says ``body``.

    def build(self, ctx: BuildContext) -> Step:
        return Repeat(ctx.value(self.count), ctx.step(self.body), name=self.name, parameter=self.parameter)

    def render(self, ctx: RenderContext) -> str:
        count = ctx.value(self.count, f"{self.label()}.count")
        name = f", name={self.name!r}" if self.name else ""
        return f"{ctx.use(Repeat)}({count}, {ctx.step(self.body)}{name}, parameter={self.parameter!r})"


class Sweep(Step):
    """Run a child once per value, with ``parameter`` bound to the value.

    ``also`` binds more parameters alongside, one value per sweep value:
    ``also={"bias_voltage_leg": [0, 0, 1, 1]}`` records which leg of a
    there-and-back sweep each point was taken on.
    """

    determinate = True

    def __init__(
        self,
        parameter: str,
        values: Iterable[object],
        child_factory: Callable[[object], Step],
        name: str | None = None,
        also: Mapping[str, Seq[object]] | None = None,
    ) -> None:
        super().__init__(name=name)
        self.parameter = parameter
        self.values = list(values)
        self.also = {key: list(column) for key, column in (also or {}).items()}
        for key, column in self.also.items():
            if len(column) != len(self.values):
                raise ValueError(
                    f"Sweep {parameter!r}: also[{key!r}] has {len(column)} values for {len(self.values)} points"
                )
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
            with ExitStack() as bound:
                for key, column in self.also.items():
                    bound.enter_context(self.context.bound_parameter(key, column[index]))
                bound.enter_context(self.context.bound_parameter(self.parameter, value))
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


class SweepStepParams(StepParams):
    """Run ``body`` once per value, with ``parameter`` bound to that value.

    ``values`` is a sweep param (``{param: bias.sweep}``) or a literal list.
    Inside ``body``, ``{swept: <parameter>}`` is the current value, and every
    observation records it — and whatever the sweep's mode records beside it
    (a waypoints sweep's ``<parameter>_leg``).
    """

    type: Literal["sweep"] = "sweep"
    parameter: str = Field(json_schema_extra={"column": "records"})
    values: ParamRef | list[float]
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Sweep

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)

    # The one step the generic builder cannot do: its constructor takes a
    # closure, which builds the body afresh at each value.

    def build(self, ctx: BuildContext) -> Step:
        if isinstance(self.values, ParamRef):
            sweep = ctx.param(self.values.param)
            values, also = sweep.values(), sweep.also(self.parameter)
        else:
            values, also = list(self.values), None
        return Sweep(
            self.parameter,
            values,
            lambda value: ctx.scoped(self.parameter, value).step(self.body),
            name=self.name,
            also=also,
        )

    def render(self, ctx: RenderContext) -> str:
        where = f"{self.label()}.values"
        if isinstance(self.values, ParamRef):
            decl = ctx.params.find(self.values.param) if ctx.params is not None else None
            if ctx.params is not None and decl is None:
                ctx.problem(f"{where} reads param {self.values.param!r}, which is not declared")
            elif decl is not None and decl.type != "sweep":
                ctx.problem(f"{where} reads {self.values.param!r}, which is not a sweep param")
            sweep = f"{ctx.params_var}.{self.values.param}"
            values = f"{sweep}.values()"
            also = f", also={sweep}.also({self.parameter!r})"
        else:
            values = repr(list(self.values))
            also = ""
        inner, ident = ctx.scoped(self.parameter)
        body = inner.step(self.body)
        name = f", name={self.name!r}" if self.name else ""
        return f"{ctx.use(Sweep)}({self.parameter!r}, {values}, lambda {ident}: {body}{name}{also})"


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


class WaitStepParams(StepParams):
    """Wait, abortably."""

    type: Literal["wait"] = "wait"
    seconds: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return Wait


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


class WithParameterStepParams(StepParams):
    """Run ``body`` with ``parameter`` set to ``value``; its rows carry it.

    Labels part of a run — ``phase: background`` — the way a sweep labels each
    point with its value.
    """

    type: Literal["with_parameter"] = "with_parameter"
    parameter: str = Field(json_schema_extra={"column": "records"})
    value: Value
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return WithParameter

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)


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

    Each attempt binds ``parameter`` to 0, 1, 2 ..., as a repeat does: what a
    failed attempt recorded is still data, and the binding keeps it apart from
    the attempt that succeeded.
    """

    determinate = False

    def __init__(self, max_attempts: int, child: Step, name: str | None = None, *, parameter: str = "attempt") -> None:
        super().__init__(name=name)
        if max_attempts < 1:
            raise ValueError("Retry max_attempts must be at least 1")
        self.max_attempts = max_attempts
        self.child = child
        self.parameter = parameter
        self.add_child(child)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        for attempt in range(self.max_attempts):
            if self.aborted:
                return Status.ABORTED
            last_attempt = attempt == self.max_attempts - 1
            try:
                with self.context.bound_parameter(self.parameter, attempt):
                    status = self.child.execute(self.context, self.node_id, iteration=attempt)
            except Exception:
                if last_attempt:
                    raise
                continue
            if status is not Status.FAILED:
                return status  # SUCCESS, or ABORTED propagating
        return Status.FAILED


class RetryStepParams(StepParams):
    """Run ``child`` until it succeeds, up to ``max_attempts`` times; errors are retried too.

    Each attempt's rows carry ``parameter`` = 0, 1, 2 ..., so what a failed
    attempt recorded stays in the data, apart from the attempt that succeeded.
    """

    type: Literal["retry"] = "retry"
    max_attempts: Value = 3
    parameter: str = Field(default="attempt", json_schema_extra={"column": "records"})
    child: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Retry

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)


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


class IfStepParams(StepParams):
    """Run ``then`` if ``condition`` succeeds, else ``otherwise`` (or skip)."""

    type: Literal["if"] = "if"
    condition: AnyStep
    then: AnyStep
    otherwise: AnyStep | None = None

    @classmethod
    def step_class(cls) -> StepClass:
        return If


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


class SelectorStepParams(StepParams):
    """Try each child in order until one succeeds."""

    type: Literal["selector"] = "selector"
    children: list[AnyStep] = Field(default_factory=list)

    @classmethod
    def step_class(cls) -> StepClass:
        return Selector


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


class InvertStepParams(StepParams):
    """Succeed when ``child`` fails, and fail when it succeeds."""

    type: Literal["invert"] = "invert"
    child: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Invert


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


class ValueAboveStepParams(StepParams):
    """Succeed if the latest recorded ``field`` is above ``threshold``."""

    type: Literal["value_above"] = "value_above"
    field: str = Field(json_schema_extra={"column": "reads"})
    threshold: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return ValueAbove


class ValueBelow(_Comparison):
    """SUCCESS if the latest ``field`` is below ``threshold``, else FAILED."""

    def run(self) -> Status:
        return Status.SUCCESS if self._latest() < self.threshold else Status.FAILED


class ValueBelowStepParams(StepParams):
    """Succeed if the latest recorded ``field`` is below ``threshold``."""

    type: Literal["value_below"] = "value_below"
    field: str = Field(json_schema_extra={"column": "reads"})
    threshold: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return ValueBelow
