"""Shared, typed sweep parameters for measurements and procedures.

A sweep is a 1-D sequence of set-points. Three shapes are supported and
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

    bias:
      sweep:
        mode: waypoints     # WaypointSweepParams: there and back
        points: [0.0, 1.4, 0.0, -1.4, 0.0]
        step: 0.005

All expose :meth:`values`, so a measurement consumes the set-points without
caring which shape authored them. :meth:`also` is what a sweep records beside
each value: for waypoints, which leg of the path it was on. YAML stores the *values* (or the rule that
generates them); Python decides how to step through them.

The fields carry no unit. The same sweep drives a bias in volts and an
attenuation in decibels, so the unit belongs to what is swept — a param's
``unit`` in a procedure definition, a field description in a measurement. The
original volt-suffixed names (``start_V``, ``stop_V``, ``step_V``,
``values_V``) are still accepted when loading, so existing projects and presets
are unaffected; they are written under the new names.
"""

from __future__ import annotations

import math
from typing import Annotated, Literal, Union

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


def _line(start: float, stop: float, step: float) -> list[float]:
    """``start`` to ``stop`` inclusive, ``step`` apart (the last step may be short)."""
    if step <= 0:
        raise ValueError("step must be a positive magnitude")
    span = stop - start
    if span == 0:
        return [start]
    signed = step if span > 0 else -step
    n = int(round(span / signed))
    # Rounded well below the sweep's own scale: 0.005 * 280 is
    # 1.4000000000000001 in floating point, and walking back to 0 lands on
    # 2e-16, while a set-point is recorded, and filtered by, as written.
    digits = 12 - math.floor(math.log10(max(abs(start), abs(stop), step)))
    points = [round(start + i * signed, digits) + 0.0 for i in range(n + 1)]
    tol = 1e-9 * max(1.0, abs(stop))
    if abs(points[-1] - stop) > tol:
        points.append(stop)
    return points


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
        return _line(self.start, self.stop, self.step)

    def also(self, parameter: str) -> dict[str, list[int]]:
        return {}


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

    def also(self, parameter: str) -> dict[str, list[int]]:
        return {}


class WaypointSweepParams(BaseModel):
    """A path through turning points, walked in straight legs ``step`` apart.

    ``[0, 1.4, 0, -1.4, 0]`` is an IV loop: up to +1.4, back to 0, down to
    -1.4, back to 0. Each turning point is visited once, as the end of the leg
    that reaches it. Every value is recorded with its leg (0, 1, 2, ...) in
    ``<parameter>_leg``, so the legs of a hysteretic curve can be told apart,
    filtered and drawn one line each.
    """

    mode: Literal["waypoints"] = "waypoints"
    points: list[float] = Field(default_factory=lambda: [0.0, 1.0, 0.0], min_length=2)
    step: float = Field(default=0.01, gt=0)

    def _legs(self) -> tuple[list[float], list[int]]:
        values: list[float] = [self.points[0]]
        legs: list[int] = [0]
        leg = 0
        for start, stop in zip(self.points, self.points[1:]):
            if start == stop:
                continue
            walked = _line(start, stop, self.step)[1:]
            values += walked
            legs += [leg] * len(walked)
            leg += 1
        return values, legs

    def values(self) -> list[float]:
        return self._legs()[0]

    def also(self, parameter: str) -> dict[str, list[int]]:
        return {f"{parameter}_leg": self._legs()[1]}


SweepParams = Annotated[
    Union[LinearSweepParams, ExplicitSweepParams, WaypointSweepParams],
    Field(discriminator="mode"),
]
"""A sweep that is linear, explicit, or waypoints, discriminated on ``mode``."""
