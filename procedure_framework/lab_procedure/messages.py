from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


NodeId = tuple[str, ...]


def now() -> datetime:
    """The current time, in UTC. Every message is stamped with it."""
    return datetime.now(UTC)


@dataclass(frozen=True)
class RunStarted:
    """Everything known about a run before its first reading."""

    procedure: str
    device: str | None = None
    operator: str | None = None
    notes: str | None = None
    # The project the run belongs to, by directory name.
    project: str | None = None
    # The setup the run was taken on, by name, and a copy of its fields as they
    # were when it started: {"bias_resistor": {"value": 100, "unit": "kΩ"}}.
    setup: str | None = None
    setup_fields: dict[str, Any] = field(default_factory=dict)
    # Which setup field fills each of the procedure's needs:
    # {"bias_resistance": "channel2.bias_resistor"}.
    setup_needs: dict[str, str] = field(default_factory=dict)
    # The procedure definition that built the step tree, as data.
    definition: dict[str, Any] | None = None
    # The measurement's own params: the sweep, the gate time.
    params: dict[str, Any] = field(default_factory=dict)
    # How each instrument was configured when the run started, by role.
    instruments: dict[str, Any] = field(default_factory=dict)
    # The columns the run's rows can carry: {name: {"unit": ...}}.
    columns: dict[str, Any] = field(default_factory=dict)
    t: datetime = field(default_factory=now)


@dataclass(frozen=True)
class Point:
    """One row of a run's data: everything recorded at one set of parameters.

    ``values`` holds the parameters that were in force (the swept values and
    labels such as ``phase``) followed by every field recorded under them, so
    a nested sweep produces more rows, never a nested structure. ``seq`` counts
    rows in the order they were recorded, ``t`` is when the row's last reading
    was taken, and ``steps`` names the steps that recorded into it. See
    :meth:`RunContext.observe` for how readings become rows.
    """

    seq: int
    t: datetime
    values: dict[str, Any]
    steps: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunEnded:
    status: str
    t: datetime = field(default_factory=now)


@dataclass(frozen=True)
class StepFailed:
    node_id: NodeId
    error: str


@dataclass(frozen=True)
class StepBegan:
    node_id: NodeId
    parent_id: NodeId | None
    label: str
    determinate: bool
    # The step's type as a definition names it: "sweep", "count".
    kind: str = ""
    t: datetime = field(default_factory=now)


@dataclass(frozen=True)
class StepProgress:
    node_id: NodeId
    fraction: float
    detail: str | None = None


@dataclass(frozen=True)
class StepEnded:
    node_id: NodeId
    status: str
    # What was raised, when the step ended because of an exception.
    error: str | None = None
    t: datetime = field(default_factory=now)
