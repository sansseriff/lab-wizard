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
    run_type: str
    device: str | None = None
    cryostat: str | None = None
    operator: str | None = None
    description: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    # How the instruments themselves were configured when the run started —
    # {name: params}. The measurement's own params are `config`.
    instruments: dict[str, Any] = field(default_factory=dict)


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
    t: datetime = field(default_factory=now)
