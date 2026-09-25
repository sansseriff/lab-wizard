"""Generic, reusable procedure steps that drive lab_wizard instruments.

These wrap the concrete instrument contracts
(:class:`~lab_wizard.lib.instruments.general.vsource.VSource`) as
:class:`~lab_procedure.Step` nodes so measurements can compose them with
``Sequence``/``Sweep``/``Wait``. They carry no measurement-specific logic. The
few that take readings (``Count``, ``ReadVoltage``) record them with
``RunContext.observe``, which turns them into rows.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from lab_procedure import Status, Step

from lab_wizard.lib.instruments.general.attenuator import Attenuator
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.laser import Laser
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource

logger = logging.getLogger(__name__)


def _record_reached(step: Step, read_back) -> None:
    """Record what a setter actually reached, under ``step.record``, if it names a field.

    Hardware quantizes and clamps: an attenuator asked for 12.34567 dB sits at
    12.346, and a non-linear one may be further off. What it reached is a
    measurement, so it is recorded like one, beside the swept value it was
    asked for.
    """
    if step.record:
        assert step.context is not None
        step.context.observe({step.record: read_back()})


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

    def __init__(
        self, counter: Counter, threshold_mV: float, name: str | None = None, *, record: str | None = None
    ) -> None:
        super().__init__(name=name)
        self.counter = counter
        self.threshold_mV = threshold_mV
        self.record = record

    def run(self) -> Status:
        # A threshold the counter refused would make every later count
        # meaningless, so a refusal fails the step rather than being ignored.
        if self.counter.set_threshold(self.threshold_mV) is False:
            return Status.FAILED
        _record_reached(self, self.counter.get_threshold)
        return Status.SUCCESS


class SetAttenuation(Step):
    """Set an attenuator's attenuation, in dB."""

    def __init__(
        self, attenuator: Attenuator, attenuation_db: float, name: str | None = None, *, record: str | None = None
    ) -> None:
        super().__init__(name=name)
        self.attenuator = attenuator
        self.attenuation_db = attenuation_db
        self.record = record

    def run(self) -> Status:
        if self.attenuator.set_attenuation(self.attenuation_db) is False:
            return Status.FAILED
        _record_reached(self, self.attenuator.get_attenuation)
        return Status.SUCCESS


class OpenShutter(Step):
    """Let light through an attenuator."""

    def __init__(self, attenuator: Attenuator, name: str | None = None) -> None:
        super().__init__(name=name)
        self.attenuator = attenuator

    def run(self) -> Status:
        return Status.FAILED if self.attenuator.open_shutter() is False else Status.SUCCESS


class CloseShutter(Step):
    """Block light through an attenuator."""

    def __init__(self, attenuator: Attenuator, name: str | None = None) -> None:
        super().__init__(name=name)
        self.attenuator = attenuator

    def run(self) -> Status:
        return Status.FAILED if self.attenuator.close_shutter() is False else Status.SUCCESS


class LaserOn(Step):
    """Start a laser emitting."""

    def __init__(self, laser: Laser, name: str | None = None) -> None:
        super().__init__(name=name)
        self.laser = laser

    def run(self) -> Status:
        return Status.FAILED if self.laser.turn_on() is False else Status.SUCCESS


class LaserOff(Step):
    """Stop a laser emitting."""

    def __init__(self, laser: Laser, name: str | None = None) -> None:
        super().__init__(name=name)
        self.laser = laser

    def run(self) -> Status:
        return Status.FAILED if self.laser.turn_off() is False else Status.SUCCESS


class SetLaserPower(Step):
    """Set a laser's output power, in dBm."""

    def __init__(
        self, laser: Laser, power_dbm: float, name: str | None = None, *, record: str | None = None
    ) -> None:
        super().__init__(name=name)
        self.laser = laser
        self.power_dbm = power_dbm
        self.record = record

    def run(self) -> Status:
        if self.laser.set_power_dbm(self.power_dbm) is False:
            return Status.FAILED
        _record_reached(self, self.laser.get_power_dbm)
        return Status.SUCCESS


class Count(Step):
    """Count for one gate and record ``counts``, ``int_time`` and ``count_rate``.

    The generic counterpart of a measurement-specific counting step. The swept
    parameters in force go into the row too (``RunContext.observe``), so a
    count inside a bias sweep is a row carrying its bias.
    """

    emits = ("counts", "int_time", "count_rate")

    def __init__(self, counter: Counter, gate_time: float, name: str | None = None) -> None:
        super().__init__(name=name)
        self.counter = counter
        self.gate_time = gate_time

    def run(self) -> Status:
        assert self.context is not None
        counts = self.counter.count(self.gate_time)
        self.context.observe(
            {
                "counts": counts,
                "int_time": self.gate_time,
                "count_rate": counts / self.gate_time if self.gate_time else 0.0,
            }
        )
        return Status.SUCCESS


class ReadVoltage(Step):
    """Read a voltmeter and record the reading under ``field``."""

    def __init__(self, sense: VSense, field: str = "voltage", name: str | None = None) -> None:
        super().__init__(name=name)
        self.sense = sense
        self.field = field

    def run(self) -> Status:
        assert self.context is not None
        self.context.observe({self.field: self.sense.get_voltage()})
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


def _report_exit_failure(status: Status, what: str, detail: str) -> None:
    """Raise, or log if the body already failed.

    Cleanup that fails after a successful body is an error in its own right.
    After a failed or aborted body it must not raise: ``on_exit`` runs inside
    ``Step.execute``'s ``finally``, so a new exception would replace the body's
    — and the body's is the one worth reading.
    """
    if status is Status.SUCCESS:
        raise RuntimeError(f"Could not {what}: {detail}")
    logger.error("Could not %s after %s: %s", what, status.value, detail)


class SafeGuard(Step):
    """Run a body, then put an instrument into its declared safe state.

    The safe state comes from the instrument's behavior — ``VSource`` is 0 V and
    output off, ``Attenuator`` is shutter closed at maximum attenuation — so one
    guard works for every behavior that declares one. It runs in ``on_exit``,
    which :meth:`Step.execute` always calls from its ``finally``, because a
    sibling cleanup step after the body would be skipped the moment the body
    aborts or fails.
    """

    def __init__(self, instrument: object, body: Step, *, name: str | None = None) -> None:
        super().__init__(name=name)
        # Checked on the class, not the instance: a remote proxy answers any
        # attribute name reflectively, so the instance would always say yes.
        if not callable(getattr(type(instrument), "enter_safe_state", None)):
            raise TypeError(
                f"{type(instrument).__name__} declares no safe state, so "
                "SafeGuard has nothing to return it to"
            )
        self.instrument = instrument
        self.body = body
        self.add_child(body)

    def run(self) -> Status:
        assert self.context is not None
        assert self.node_id is not None
        return self.body.execute(self.context, self.node_id, position=0)

    def on_exit(self, status: Status) -> None:
        self._attempt(status, "enter the safe state", getattr(self.instrument, "enter_safe_state"))

    def _attempt(self, status: Status, what: str, action) -> None:
        try:
            ok = action()
        except Exception as exc:  # noqa: BLE001 - reported below, never swallowed
            _report_exit_failure(status, what, repr(exc))
            return
        if ok is False:
            _report_exit_failure(status, what, "the instrument reported failure")


class SourceGuard(SafeGuard):
    """Run a body with the source enabled, guaranteeing safe shutdown.

    Turns the source on (optionally) on enter, runs the single ``body`` child,
    and on exit — *even on failure or abort* — returns to zero and/or turns the
    output off. With both exit flags set, that is exactly ``VSource``'s declared
    safe state. Clearing one is a deliberate choice a measurement exposes (the
    IV curve's ``safety`` params do), so it is honoured rather than overridden.
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
        super().__init__(source, body, name=name)
        self.source = source
        self.turn_on_at_start = turn_on_at_start
        self.return_to_zero = return_to_zero
        self.turn_off_at_end = turn_off_at_end

    def on_enter(self) -> None:
        if self.turn_on_at_start:
            self.source.turn_on()

    def on_exit(self, status: Status) -> None:
        if self.return_to_zero and self.turn_off_at_end:
            super().on_exit(status)
            return
        if self.return_to_zero:
            self._attempt(status, "return the source to 0 V", lambda: self.source.set_voltage(0.0))
        if self.turn_off_at_end:
            self._attempt(status, "turn the source off", self.source.turn_off)


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
        _report_exit_failure(status, "restore overridden settings", "; ".join(failures))
