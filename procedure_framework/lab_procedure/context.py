from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from lab_procedure.bus import MessageBus
from lab_procedure.messages import Observation


_MISSING = object()


@dataclass
class RunContext:
    data_bus: MessageBus = field(default_factory=MessageBus)
    status_bus: MessageBus = field(default_factory=MessageBus)
    instruments: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    sweep_index: int | None = None
    sequence_index: int = 0
    # The most recent value recorded under each data field, across the run.
    # Condition steps read it — "is the last count rate above 1e6?" — so a
    # procedure can branch on what it just measured without an expression
    # language. Updated by ``observe``.
    latest: dict[str, Any] = field(default_factory=dict)

    def set_parameter(self, name: str, value: Any) -> None:
        self.parameters[name] = value

    def snapshot_parameters(self) -> dict[str, Any]:
        return dict(self.parameters)

    @contextmanager
    def bound_parameter(self, name: str, value: Any) -> Iterator[None]:
        previous = self.parameters.get(name, _MISSING)
        self.parameters[name] = value
        try:
            yield
        finally:
            if previous is _MISSING:
                self.parameters.pop(name, None)
            else:
                self.parameters[name] = previous

    def observe(self, data: dict[str, Any]) -> Observation:
        """Emit one observation of ``data`` and remember its values.

        The swept parameters in force are copied into ``data`` as well as
        ``metadata``, so every observation is one flat row: a nested sweep
        produces more rows, never a nested structure, and "plot count rate
        against bias" is a choice of two column names.
        """
        snapshot = self.snapshot_parameters()
        observation = Observation(
            data={**snapshot, **data},
            metadata=snapshot,
            sequence_index=self.next_sequence_index(),
            sweep_index=self.sweep_index,
        )
        self.latest.update(data)
        self.data_bus.emit(observation)
        return observation

    def next_sequence_index(self) -> int:
        index = self.sequence_index
        self.sequence_index += 1
        return index
