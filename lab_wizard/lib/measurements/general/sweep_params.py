"""Shared, typed sweep parameters for measurements and procedures.

A sweep is a 1-D sequence of set-points. Two shapes are supported and
discriminated on a ``mode`` field so they round-trip cleanly through YAML:

    bias:
      sweep:
        mode: linear        # LinearSweepParams
        start: 0.0
        stop: 1.4
        step: 0.005

    bias:
      sweep:
        mode: explicit      # ExplicitSweepParams
        values: [0.0, 0.1, 0.25, 0.5]

Both expose :meth:`values`, so a measurement consumes the set-points without
caring which shape authored them. YAML stores the *values* (or the rule that
generates them); Python decides how to step through them.

The fields carry no unit. The same sweep drives a bias in volts and an
attenuation in decibels, so the unit belongs to what is swept — a param's
``unit`` in a procedure definition, a field description in a measurement. The
original volt-suffixed names (``start_V``, ``stop_V``, ``step_V``,
``values_V``) are still accepted when loading, so existing projects and presets
are unaffected; they are written under the new names.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LinearSweepParams(BaseModel):
    """Evenly-spaced sweep defined by endpoints and a step.

    ``stop`` is inclusive: the endpoint is always emitted even when it is not
    an exact multiple of ``step`` from ``start``. ``step`` is treated as a
    magnitude; the direction is inferred from ``start``/``stop``.
    """

    model_config = ConfigDict(populate_by_name=True)

    mode: Literal["linear"] = "linear"
    start: float = Field(default=0.0, validation_alias=AliasChoices("start", "start_V"))
    stop: float = Field(default=1.0, validation_alias=AliasChoices("stop", "stop_V"))
    step: float = Field(default=0.01, validation_alias=AliasChoices("step", "step_V"))

    def values(self) -> list[float]:
        if self.step <= 0:
            raise ValueError("step must be a positive magnitude")
        span = self.stop - self.start
        if span == 0:
            return [self.start]
        step = self.step if span > 0 else -self.step
        n = int(round(span / step))
        points = [self.start + i * step for i in range(n + 1)]
        tol = 1e-9 * max(1.0, abs(self.stop))
        if abs(points[-1] - self.stop) > tol:
            points.append(self.stop)
        return points


class ExplicitSweepParams(BaseModel):
    """An explicit, ordered list of sweep set-points."""

    # Written under the alias by default, so a dump — project YAML, a preset —
    # says ``values`` rather than the internal ``points``.
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    mode: Literal["explicit"] = "explicit"
    # ``values`` is also the method name, so the field is stored as ``points``
    # and appears in YAML under ``values`` (or the older ``values_V``).
    points: list[float] = Field(
        default_factory=list,
        validation_alias=AliasChoices("values", "values_V", "points"),
        serialization_alias="values",
    )

    def values(self) -> list[float]:
        return list(self.points)


SweepParams = Annotated[
    Union[LinearSweepParams, ExplicitSweepParams],
    Field(discriminator="mode"),
]
"""A sweep that is either linear or explicit, discriminated on ``mode``."""
