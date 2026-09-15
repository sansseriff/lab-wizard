"""Generic, reusable procedure steps that drive lab_wizard instruments.

These wrap the concrete instrument contracts
(:class:`~lab_wizard.lib.instruments.general.vsource.VSource`) as
:class:`~lab_procedure.Step` nodes so measurements can compose them with
``Sequence``/``Sweep``/``Wait``. They carry no measurement-specific logic and
emit no :class:`~lab_procedure.Observation`; data-producing steps live with
their measurement.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from lab_procedure import Status, Step

from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.vsource import VSource

logger = logging.getLogger(__name__)


class SetVoltage(Step):
    """Set the source output to a fixed voltage."""

    def __init__(self, source: VSource, voltage: float, name: str | None = None) -> None:
        super().__init__(name=name)
        self.source = source
        self.voltage = voltage

    def run(self) -> Status:
        self.source.set_voltage(self.voltage)
        return Status.SUCCESS


class SetThreshold(Step):
    """Set a counter's discriminator threshold, in millivolts.

    A procedure that counts sets its own threshold rather than inheriting
    whatever the counter holds: on a server-held counter, "whatever it holds" is
    what the previous client left. See ``plans/procedure_plan.md`` 0.8.
    """

    def __init__(self, counter: Counter, threshold_mV: float, name: str | None = None) -> None:
        super().__init__(name=name)
        self.counter = counter
        self.threshold_mV = threshold_mV

    def run(self) -> Status:
        # A threshold the counter refused would make every later count
        # meaningless, so a refusal fails the step rather than being ignored.
        if self.counter.set_threshold(self.threshold_mV) is False:
            return Status.FAILED
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


class WithSettings(Step):
    """Run a body with instrument settings overridden, restoring them on exit.

    ``overrides`` maps a setting name to the value to use for the body. A
    setting ``x`` means the instrument offers ``set_x(value)`` and ``get_x()``:
    on enter each prior value is read back and the override applied; on exit —
    *even on failure or abort* — the read-back values are restored in reverse
    order. Read-back rather than the configured value, because the hardware
    quantizes, and restoring what was actually there is what leaves the
    instrument as the body found it.

    This is how a procedure deviates from an instrument's configured baseline
    without the deviation leaking: it is scoped, visible in the Step tree, and
    undone. Takes a mapping rather than ``**overrides`` so that a setting named
    ``name`` cannot collide with the step's own name.

        WithSettings(counter, {"threshold": 30.0}, CountAtBias(...))
    """

    def __init__(
        self,
        instrument: object,
        overrides: Mapping[str, object],
        body: Step,
        *,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.instrument = instrument
        self.overrides = dict(overrides)
        self.body = body
        self.add_child(body)
        # Only what was actually applied is restored. ``Step.execute`` runs
        # ``on_exit`` even when ``on_enter`` raises, so a failure on the second
        # override must not "restore" a first one that never happened.
        self._applied: list[tuple[str, object]] = []

    def _method(self, verb: str, setting: str):
        method = getattr(self.instrument, f"{verb}_{setting}", None)
        if not callable(method):
            raise AttributeError(
                f"{type(self.instrument).__name__} has no {verb}_{setting}(); "
                f"WithSettings needs both set_{setting} and get_{setting} to "
                "override a setting and put it back"
            )
        return method

    def on_enter(self) -> None:
        self._applied = []
        # Resolve every method before touching hardware, so a typo in the last
        # setting cannot leave the first ones applied.
        methods = {
            setting: (self._method("get", setting), self._method("set", setting))
            for setting in self.overrides
        }
        for setting, value in self.overrides.items():
            getter, setter = methods[setting]
            prior = getter()
            if setter(value) is False:
                raise RuntimeError(
                    f"{type(self.instrument).__name__} refused set_{setting}({value!r})"
                )
            self._applied.append((setting, prior))

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        return self.body.execute(self.context, self.node_id, position=0)

    def on_exit(self, status: Status) -> None:
        failures: list[str] = []
        for setting, prior in reversed(self._applied):
            try:
                if self._method("set", setting)(prior) is False:
                    failures.append(f"set_{setting}({prior!r}) was refused")
            except Exception as exc:  # noqa: BLE001 - every restore is attempted
                failures.append(f"set_{setting}({prior!r}) raised {exc!r}")
        self._applied = []
        if not failures:
            return
        detail = "; ".join(failures)
        if status is Status.SUCCESS:
            raise RuntimeError(f"Could not restore overridden settings: {detail}")
        # The body already failed or aborted. Raising here would replace that
        # error with this one, and the original is the one worth reading.
        logger.error("Could not restore overridden settings after %s: %s", status.value, detail)
