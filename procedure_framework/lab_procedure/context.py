from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from lab_procedure.bus import MessageBus


_MISSING = object()


@dataclass
class RunContext:
    data_bus: MessageBus = field(default_factory=MessageBus)
    status_bus: MessageBus = field(default_factory=MessageBus)
    instruments: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    sweep_index: int | None = None
    sequence_index: int = 0

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

    def next_sequence_index(self) -> int:
        index = self.sequence_index
        self.sequence_index += 1
        return index
