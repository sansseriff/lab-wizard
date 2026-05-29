from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


NodeId = tuple[str, ...]


@dataclass(frozen=True)
class RunStarted:
    run_type: str
    device: str | None = None
    cryostat: str | None = None
    operator: str | None = None
    description: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    details: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    sequence_index: int | None = None
    sweep_index: int | None = None


@dataclass(frozen=True)
class RunEnded:
    status: str


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


@dataclass(frozen=True)
class StepProgress:
    node_id: NodeId
    fraction: float
    detail: str | None = None


@dataclass(frozen=True)
class StepEnded:
    node_id: NodeId
    status: str
