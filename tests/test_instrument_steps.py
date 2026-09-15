"""Generic instrument steps: SetThreshold and WithSettings.

Both exist so a procedure states the instrument settings it depends on instead
of inheriting whatever the instrument currently holds — which, on a server-held
instrument, is whatever the previous client left.
"""

from __future__ import annotations

import logging

import pytest

from lab_procedure import ProcedureRunner, Status, Step

from lab_wizard.lib.instruments.general.counter import StandInCounter
from lab_wizard.lib.task_adapters.instrument_steps import SetThreshold, WithSettings


class RecordingCounter(StandInCounter):
    """A counter that records every set call and can be told to refuse one."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, float]] = []
        self.refuse: set[str] = set()

    def set_threshold(self, threshold_mV: float) -> bool:
        self.calls.append(("threshold", threshold_mV))
        if "threshold" in self.refuse:
            return False
        return super().set_threshold(threshold_mV)

    def set_gate_time(self, gate_time: float) -> bool:
        self.calls.append(("gate_time", gate_time))
        if "gate_time" in self.refuse:
            return False
        return super().set_gate_time(gate_time)


class Probe(Step):
    """Records the counter's settings as the body sees them, then succeeds or fails."""

    def __init__(self, counter: RecordingCounter, outcome: object = Status.SUCCESS) -> None:
        super().__init__(name="probe")
        self.counter = counter
        self.outcome = outcome
        self.seen: dict[str, float] = {}

    def run(self) -> Status:
        self.seen = {
            "threshold": self.counter.get_threshold(),
            "gate_time": self.counter.get_gate_time(),
        }
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


def _run(step: Step) -> Status:
    return ProcedureRunner().run(step)


# --------------------------- SetThreshold ---------------------------


def test_set_threshold_sets_it():
    counter = RecordingCounter()
    assert _run(SetThreshold(counter, 30.0)) is Status.SUCCESS
    assert counter.get_threshold() == 30.0


def test_a_refused_threshold_fails_the_step():
    """Counting on at the wrong threshold would make every later point meaningless."""
    counter = RecordingCounter()
    counter.refuse.add("threshold")
    assert _run(SetThreshold(counter, 30.0)) is Status.FAILED


# --------------------------- WithSettings ---------------------------


def test_the_body_sees_the_overrides_and_they_are_restored_after():
    counter = RecordingCounter()
    counter.threshold_mV, counter.gate_time = -50.0, 1.0
    probe = Probe(counter)

    status = _run(WithSettings(counter, {"threshold": 30.0, "gate_time": 0.5}, probe))

    assert status is Status.SUCCESS
    assert probe.seen == {"threshold": 30.0, "gate_time": 0.5}
    assert (counter.threshold_mV, counter.gate_time) == (-50.0, 1.0)
    # Restored in reverse order of application.
    assert counter.calls == [
        ("threshold", 30.0),
        ("gate_time", 0.5),
        ("gate_time", 1.0),
        ("threshold", -50.0),
    ]


@pytest.mark.parametrize("outcome", [Status.FAILED, RuntimeError("body broke")])
def test_settings_are_restored_when_the_body_fails(outcome):
    counter = RecordingCounter()
    counter.threshold_mV = -50.0
    step = WithSettings(counter, {"threshold": 30.0}, Probe(counter, outcome))

    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError, match="body broke"):
            _run(step)
    else:
        assert _run(step) is Status.FAILED
    assert counter.threshold_mV == -50.0


def test_settings_are_restored_on_abort():
    counter = RecordingCounter()
    counter.threshold_mV = -50.0

    class AbortingProbe(Step):
        def run(self) -> Status:
            assert self.parent_step is not None
            self.parent_step.abort()
            return Status.ABORTED

    probe = AbortingProbe()
    step = WithSettings(counter, {"threshold": 30.0}, probe)
    probe.parent_step = step  # type: ignore[attr-defined]

    assert _run(step) is Status.ABORTED
    assert counter.threshold_mV == -50.0


def test_only_applied_overrides_are_restored_when_one_is_refused():
    """``on_exit`` runs even when ``on_enter`` raises; it must not restore a
    setting that was never changed."""
    counter = RecordingCounter()
    counter.threshold_mV, counter.gate_time = -50.0, 1.0
    counter.refuse.add("gate_time")

    with pytest.raises(RuntimeError, match="refused set_gate_time"):
        _run(WithSettings(counter, {"threshold": 30.0, "gate_time": 0.5}, Probe(counter)))

    assert counter.calls == [("threshold", 30.0), ("gate_time", 0.5), ("threshold", -50.0)]
    assert counter.threshold_mV == -50.0


def test_a_setting_without_a_getter_is_rejected_before_anything_changes():
    counter = RecordingCounter()
    counter.threshold_mV = -50.0

    with pytest.raises(AttributeError, match="get_coupling"):
        _run(WithSettings(counter, {"threshold": 30.0, "coupling": "AC"}, Probe(counter)))

    assert counter.calls == []
    assert counter.threshold_mV == -50.0


def test_a_failed_restore_after_success_is_an_error():
    counter = RecordingCounter()
    counter.threshold_mV = -50.0

    class RefuseAfter(Probe):
        def run(self) -> Status:
            self.counter.refuse.add("threshold")
            return Status.SUCCESS

    with pytest.raises(RuntimeError, match="Could not restore"):
        _run(WithSettings(counter, {"threshold": 30.0}, RefuseAfter(counter)))


def test_a_failed_restore_does_not_mask_the_bodys_own_error(caplog):
    counter = RecordingCounter()

    class BreakAndRefuse(Probe):
        def run(self) -> Status:
            self.counter.refuse.add("threshold")
            raise RuntimeError("the real problem")

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="the real problem"):
        _run(WithSettings(counter, {"threshold": 30.0}, BreakAndRefuse(counter)))
    assert "Could not restore overridden settings" in caplog.text
