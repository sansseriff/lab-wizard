"""Schemas for instrument-agnostic steps: structure, timing, and control flow."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lab_procedure import (
    If,
    Invert,
    Repeat,
    Retry,
    Selector,
    Sequence,
    Sweep,
    ValueAbove,
    ValueBelow,
    Wait,
    WithParameter,
)

from lab_wizard.lib.procedures.spec import (
    StepClass,
    AnyStep,
    ParamRef,
    RenderContext,
    StepParams,
    Value,
)


class SequenceStepParams(StepParams):
    """Run steps in order; stop at the first that does not succeed."""

    type: Literal["sequence"] = "sequence"
    children: list[AnyStep] = Field(default_factory=list)

    @classmethod
    def step_class(cls) -> StepClass:
        return Sequence


class SweepStepParams(StepParams):
    """Run ``body`` once per value, with ``parameter`` bound to that value.

    ``values`` is a sweep param (``{param: bias.sweep}``) or a literal list.
    Inside ``body``, ``{swept: <parameter>}`` is the current value, and every
    observation records it.
    """

    type: Literal["sweep"] = "sweep"
    parameter: str
    values: ParamRef | list[float]
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Sweep

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)

    def render(self, ctx: RenderContext) -> str:
        # The one step the generic renderer cannot do: its constructor takes a
        # closure, which is exactly why this is generated Python and not an
        # interpreted YAML tree.
        where = f"{self.label()}.values"
        if isinstance(self.values, ParamRef):
            decl = ctx.params.find(self.values.param) if ctx.params is not None else None
            if ctx.params is not None and decl is None:
                ctx.problems.append(f"{where} reads param {self.values.param!r}, which is not declared")
            elif decl is not None and decl.type != "sweep":
                ctx.problems.append(f"{where} reads {self.values.param!r}, which is not a sweep param")
            values = f"{ctx.params_var}.{self.values.param}.values()"
        else:
            values = repr(list(self.values))
        inner, ident = ctx.scoped(self.parameter)
        body = inner.step(self.body)
        name = f", name={self.name!r}" if self.name else ""
        return f"{ctx.use(Sweep)}({self.parameter!r}, {values}, lambda {ident}: {body}{name})"


class WithParameterStepParams(StepParams):
    """Run ``body`` with ``parameter`` set to ``value``; its rows carry it.

    Labels part of a run — ``phase: background`` — the way a sweep labels each
    point with its value.
    """

    type: Literal["with_parameter"] = "with_parameter"
    parameter: str
    value: Value
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return WithParameter

    def swept_parameters(self) -> tuple[str, ...]:
        return (self.parameter,)


class RepeatStepParams(StepParams):
    """Run ``body`` ``count`` times."""

    type: Literal["repeat"] = "repeat"
    count: Value
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Repeat

    def render(self, ctx: RenderContext) -> str:
        # Repeat's constructor calls its child argument ``child_factory``, and a
        # step instance is accepted there; the YAML says ``body``.
        count = ctx.value(self.count, f"{self.label()}.count")
        name = f", name={self.name!r}" if self.name else ""
        return f"{ctx.use(Repeat)}({count}, {ctx.step(self.body)}{name})"


class WaitStepParams(StepParams):
    """Wait, abortably."""

    type: Literal["wait"] = "wait"
    seconds: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return Wait


class RetryStepParams(StepParams):
    """Run ``child`` until it succeeds, up to ``max_attempts`` times; errors are retried too."""

    type: Literal["retry"] = "retry"
    max_attempts: Value = 3
    child: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Retry


class IfStepParams(StepParams):
    """Run ``then`` if ``condition`` succeeds, else ``otherwise`` (or skip)."""

    type: Literal["if"] = "if"
    condition: AnyStep
    then: AnyStep
    otherwise: AnyStep | None = None

    @classmethod
    def step_class(cls) -> StepClass:
        return If


class SelectorStepParams(StepParams):
    """Try each child in order until one succeeds."""

    type: Literal["selector"] = "selector"
    children: list[AnyStep] = Field(default_factory=list)

    @classmethod
    def step_class(cls) -> StepClass:
        return Selector


class InvertStepParams(StepParams):
    """Succeed when ``child`` fails, and fail when it succeeds."""

    type: Literal["invert"] = "invert"
    child: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return Invert


class ValueAboveStepParams(StepParams):
    """Succeed if the latest recorded ``field`` is above ``threshold``."""

    type: Literal["value_above"] = "value_above"
    field: str
    threshold: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return ValueAbove


class ValueBelowStepParams(StepParams):
    """Succeed if the latest recorded ``field`` is below ``threshold``."""

    type: Literal["value_below"] = "value_below"
    field: str
    threshold: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return ValueBelow
