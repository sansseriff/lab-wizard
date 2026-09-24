from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from lab_procedure.bus import MessageBus
from lab_procedure.messages import NodeId, Point, now


_MISSING = object()


@dataclass
class _OpenPoint:
    parameters: dict[str, Any]
    fields: dict[str, Any] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    t: datetime | None = None


@dataclass
class RunContext:
    data_bus: MessageBus = field(default_factory=MessageBus)
    status_bus: MessageBus = field(default_factory=MessageBus)
    instruments: Any = None
    parameters: dict[str, Any] = field(default_factory=dict)
    # The most recent value recorded under each data field, across the run.
    # Condition steps read it — "is the last count rate above 1e6?" — so a
    # procedure can branch on what it just measured without an expression
    # language. Updated by ``observe``, immediately rather than when the row
    # closes, so a condition never reads a stale value.
    latest: dict[str, Any] = field(default_factory=dict)

    _open: _OpenPoint | None = field(default=None, init=False, repr=False)
    _next_seq: int = field(default=0, init=False, repr=False)
    _executing: list[NodeId] = field(default_factory=list, init=False, repr=False)

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
            # A row never outlives the parameters it was recorded under. The
            # next reading would close it anyway; closing here, at the end of
            # the loop iteration, is what lets a live plot show each point as
            # soon as it is complete rather than one point late.
            if self._open is not None and self._open.parameters != self.parameters:
                self.close_point()

    # ------------------------------------------------------------------ rows

    def observe(self, fields: Mapping[str, Any]) -> None:
        """Record ``fields``, merging them into the current row.

        A row is everything recorded while the same parameter values were in
        force, until a field would be recorded twice. So a count and a voltage
        read at the same bias share a row; ten counts in a ``Repeat`` are ten
        rows, because each repetition binds its own index; and two procedures
        that take the same readings at the same parameter values produce the
        same rows whatever the order of their loops. Only ``seq``, ``t`` and
        ``steps`` record the order.

        The row is emitted as a :class:`Point` when it closes: when the
        parameters change, when a field would be recorded again, or when the
        run ends (:meth:`close_point`).
        """
        shadowed = sorted(self.parameters.keys() & fields.keys())
        if shadowed:
            raise ValueError(
                f"{self.current_step or 'A step'} records {', '.join(map(repr, shadowed))}, "
                "which is already a parameter in force (bound by a sweep, repeat or "
                "with_parameter). Every row carries its parameters; record the "
                "reading under a different name."
            )
        self.latest.update(fields)

        open_ = self._open
        if (
            open_ is None
            or open_.parameters != self.parameters
            or open_.fields.keys() & fields.keys()
        ):
            self.close_point()
            open_ = self._open = _OpenPoint(parameters=self.snapshot_parameters())
        open_.fields.update(fields)
        open_.t = now()
        step = self.current_step
        if step is not None and step not in open_.steps:
            open_.steps.append(step)

    def close_point(self) -> Point | None:
        """Emit the open row, if there is one. The runner calls this at run end."""
        open_ = self._open
        if open_ is None:
            return None
        self._open = None
        point = Point(
            seq=self._next_seq,
            t=open_.t or now(),
            values={**open_.parameters, **open_.fields},
            steps=tuple(open_.steps),
        )
        self._next_seq += 1
        self.data_bus.emit(point)
        return point

    # ------------------------------------------------------------ execution

    @property
    def current_step(self) -> str | None:
        """The path of the step executing now, as ``a/b[1]/c#2``."""
        return "/".join(self._executing[-1]) if self._executing else None

    @contextmanager
    def executing(self, node_id: NodeId) -> Iterator[None]:
        """Mark ``node_id`` as the executing step for the duration."""
        self._executing.append(node_id)
        try:
            yield
        finally:
            self._executing.pop()
