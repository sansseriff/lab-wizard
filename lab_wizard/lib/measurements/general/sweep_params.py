"""Shared, typed sweep parameters for measurements.

A sweep is a 1-D sequence of set-points (volts, by convention). Two shapes are
supported and discriminated on a ``mode`` field so they round-trip cleanly
through YAML:

    bias:
      sweep:
        mode: linear        # LinearSweepParams
        start_V: 0.0
        stop_V: 1.4
        step_V: 0.005

    bias:
      sweep:
        mode: explicit      # ExplicitSweepParams
        values_V: [0.0, 0.1, 0.25, 0.5]

Both expose :meth:`values`, so a measurement consumes the set-points without
caring which shape authored them. YAML stores the *values* (or the rule that
generates them); Python decides how to step through them.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class LinearSweepParams(BaseModel):
    """Evenly-spaced sweep defined by endpoints and a step.

    ``stop_V`` is inclusive: the endpoint is always emitted even when it is not
    an exact multiple of ``step_V`` from ``start_V``. ``step_V`` is treated as a
    magnitude; the direction is inferred from ``start_V``/``stop_V``.
    """

    mode: Literal["linear"] = "linear"
    start_V: float = 0.0
    stop_V: float = 1.0
    step_V: float = 0.01

    def values(self) -> list[float]:
        if self.step_V <= 0:
            raise ValueError("step_V must be a positive magnitude")
        span = self.stop_V - self.start_V
        if span == 0:
            return [self.start_V]
        step = self.step_V if span > 0 else -self.step_V
        n = int(round(span / step))
        points = [self.start_V + i * step for i in range(n + 1)]
        tol = 1e-9 * max(1.0, abs(self.stop_V))
        if abs(points[-1] - self.stop_V) > tol:
            points.append(self.stop_V)
        return points


class ExplicitSweepParams(BaseModel):
    """An explicit, ordered list of sweep set-points."""

    mode: Literal["explicit"] = "explicit"
    values_V: list[float] = Field(default_factory=list)

    def values(self) -> list[float]:
        return list(self.values_V)


SweepParams = Annotated[
    Union[LinearSweepParams, ExplicitSweepParams],
    Field(discriminator="mode"),
]
"""A sweep that is either linear or explicit, discriminated on ``mode``."""
