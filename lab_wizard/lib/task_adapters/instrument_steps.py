"""Generic, reusable procedure steps that drive lab_wizard instruments.

These wrap the concrete instrument contracts
(:class:`~lab_wizard.lib.instruments.general.vsource.VSource`) as
:class:`~lab_procedure.Step` nodes so measurements can compose them with
``Sequence``/``Sweep``/``Wait``. They carry no measurement-specific logic and
emit no :class:`~lab_procedure.Observation`; data-producing steps live with
their measurement.
"""

from __future__ import annotations

from lab_procedure import Status, Step

from lab_wizard.lib.instruments.general.vsource import VSource


class SetVoltage(Step):
    """Set the source output to a fixed voltage."""

    def __init__(self, source: VSource, voltage: float, name: str | None = None) -> None:
        super().__init__(name=name)
        self.source = source
        self.voltage = voltage

    def run(self) -> Status:
        self.source.set_voltage(self.voltage)
        return Status.SUCCESS


class TurnOn(Step):
    """Enable the source output."""

    def __init__(self, source: VSource, name: str | None = None) -> None:
        super().__init__(name=name)
        self.source = source

    def run(self) -> Status:
        self.source.turn_on()
        return Status.SUCCESS


class ReturnToZeroAndOff(Step):
    """Drive the source to 0 V and disable its output.

    Intended for cleanup: drop ``return_to_zero``/``turn_off`` independently so
    a procedure can return to zero without turning off, or vice versa.
    """

    def __init__(
        self,
        source: VSource,
        *,
        return_to_zero: bool = True,
        turn_off: bool = True,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.source = source
        self.return_to_zero = return_to_zero
        self.turn_off = turn_off

    def run(self) -> Status:
        if self.return_to_zero:
            self.source.set_voltage(0.0)
        if self.turn_off:
            self.source.turn_off()
        return Status.SUCCESS


class SourceGuard(Step):
    """Run a body with the source enabled, guaranteeing safe shutdown.

    Turns the source on (optionally) on enter, runs the single ``body`` child,
    and on exit — *even on failure or abort* — returns to zero and/or turns the
    output off. Shutdown lives in ``on_exit`` (which
    :meth:`Step.execute` always runs in its ``finally``) precisely because a
    sibling cleanup step after the body would be skipped the moment the body
    aborts or fails.
    """

    def __init__(
        self,
        source: VSource,
        body: Step,
        *,
        turn_on_at_start: bool = True,
        return_to_zero: bool = True,
        turn_off_at_end: bool = True,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.source = source
        self.body = body
        self.turn_on_at_start = turn_on_at_start
        self.return_to_zero = return_to_zero
        self.turn_off_at_end = turn_off_at_end
        self.add_child(body)

    def on_enter(self) -> None:
        if self.turn_on_at_start:
            self.source.turn_on()

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        return self.body.execute(self.context, self.node_id, position=0)

    def on_exit(self, status: Status) -> None:
        if self.return_to_zero:
            self.source.set_voltage(0.0)
        if self.turn_off_at_end:
            self.source.turn_off()
