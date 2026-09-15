"""The instrument contract a run relies on: baseline, safe state, and queries.

``apply_baseline`` puts bench settings back to the configured params at the
start of a run; ``enter_safe_state`` is what a failed run is left in;
``_query_methods_`` names the calls that only read.
"""

from __future__ import annotations

import inspect
from typing import Any, cast

import pytest

from lab_procedure import ProcedureRunner, Status, Step

from lab_wizard.lib.client.proxies.attenuator import RemoteAttenuator
from lab_wizard.lib.client.proxies.counter import RemoteCounter
from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource
from lab_wizard.lib.client.session import Session
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201ASlotDep
from lab_wizard.lib.instruments.andoAQ8201A.modules.attenuator31 import (
    Attenuator31,
    Attenuator31Params,
)
from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounter, FakeCounterParams
from lab_wizard.lib.instruments.general.attenuator import Attenuator, StandInAttenuator
from lab_wizard.lib.instruments.general.behavior import InstrumentBehavior
from lab_wizard.lib.instruments.general.counter import Counter, StandInCounter
from lab_wizard.lib.instruments.general.state_effects import collect_query_methods
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource, VSource
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.attenuator import (
    YokoAttenuator,
    YokoAttenuatorParams,
)
from lab_wizard.lib.task_adapters.instrument_steps import SafeGuard, SourceGuard


# --------------------------- apply_baseline ---------------------------


def _counter() -> FakeCounter:
    params = FakeCounterParams(
        ip_address="sim://baseline-counter",
        channels={0: Keysight53220AChannelParams(threshold_mV=-50.0, coupling="DC")},
    )
    return params.create_inst()


def test_the_default_baseline_is_a_successful_no_op():
    """Right for an instrument whose params are all connection and identity."""
    assert StandInVSource().apply_baseline() is True


def test_a_counter_input_baseline_undoes_a_previous_callers_changes():
    counter = _counter()
    channel = counter[0]
    channel.set_threshold(400.0)
    channel.set_coupling("AC")

    assert channel.apply_baseline() is True

    assert channel.get_threshold() == pytest.approx(-50.0)
    hardware = counter.virtual.inputs[1]
    assert hardware.coupling == "DC"
    assert hardware.auto_level is False
    assert hardware.threshold_v == pytest.approx(-0.050)


def test_an_input_baseline_leaves_the_counters_shared_trigger_alone():
    """A run bound to one input has no claim on the box-level trigger."""
    counter = _counter()
    counter.configure_trigger(source="external")
    counter[0].apply_baseline()
    assert counter.settings.trigger_source == "external"


def test_the_whole_counter_baseline_restores_trigger_gate_and_every_input():
    counter = _counter()
    counter.configure_trigger(source="external")
    counter.configure_gate(source="input2")
    counter[0].set_threshold(400.0)
    counter[0].count(0.01)  # arms, so there is a cached setup to forget

    assert counter.apply_baseline() is True

    assert counter.settings.trigger_source == "immediate"
    assert counter.settings.gate_source == "time"
    assert counter.virtual.trigger_source == "IMM"
    assert counter[0].get_threshold() == pytest.approx(-50.0)
    assert counter._armed is None, "the gate is re-written at the next arm"


def test_the_old_restore_name_still_works():
    counter = _counter()
    counter[0].set_threshold(400.0)
    assert counter[0].restore_configured_settings() is True
    assert counter[0].get_threshold() == pytest.approx(-50.0)


class Recorder:
    def __init__(self, slot: int = 2) -> None:
        self.slot, self.offline, self.writes = slot, False, []

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)

    def query(self, cmd: str) -> str:
        return ""


def test_attenuator_baselines_finally_send_the_configured_wavelength():
    yoko_dep, ando_dep = Recorder(), Recorder()
    yoko = YokoAttenuator(
        cast(YokoAQ2212SlotDep, yoko_dep), YokoAttenuatorParams(slot="2", wavelength_nm=1310.0)
    )
    ando = Attenuator31(
        cast(AndoAQ8201ASlotDep, ando_dep), Attenuator31Params(slot="2", wavelength_nm=1310.0)
    )

    assert yoko.apply_baseline() is True
    assert ando.apply_baseline() is True
    assert yoko_dep.writes == ["INP2:WAV +1310.0E-009"]
    assert ando_dep.writes == ["AW 1310"]


# --------------------------- through a proxy ---------------------------


class RecordingSession:
    def __init__(self) -> None:
        self.methods: list[str] = []

    def call_inst(self, path: str, method: str, args: list, kwargs: dict) -> Any:
        self.methods.append(method)
        return True


@pytest.mark.parametrize("proxy_cls", [RemoteVSource, RemoteVSense, RemoteCounter, RemoteAttenuator])
def test_a_proxy_forwards_baseline_to_the_servers_instrument(proxy_cls):
    """Concrete on the ABC, but a no-op client-side would reset nothing."""
    session = RecordingSession()
    proxy = proxy_cls(cast(Session, session), "inst://abc")
    assert proxy.apply_baseline() is True
    assert session.methods == ["apply_baseline"]


def test_a_remote_source_safe_state_runs_as_recorded_calls():
    session = RecordingSession()
    RemoteVSource(cast(Session, session), "inst://abc").enter_safe_state()
    assert session.methods == ["set_voltage", "turn_off"]


# --------------------------- VSource safe state ---------------------------


class CountingSource(StandInVSource):
    def __init__(self, *, zero_ok: bool = True) -> None:
        super().__init__()
        self.calls: list[str] = []
        self.zero_ok = zero_ok

    def set_voltage(self, voltage: float) -> bool:
        self.calls.append(f"set_voltage({voltage})")
        super().set_voltage(voltage)
        return self.zero_ok

    def turn_on(self) -> bool:
        self.calls.append("turn_on")
        return super().turn_on()

    def turn_off(self) -> bool:
        self.calls.append("turn_off")
        return super().turn_off()


def test_a_source_goes_to_zero_before_turning_off():
    source = CountingSource()
    source.voltage, source.output_enabled = 0.03, True
    assert source.enter_safe_state() is True
    assert source.calls == ["set_voltage(0.0)", "turn_off"]
    assert (source.voltage, source.output_enabled) == (0.0, False)


def test_a_source_still_turns_off_when_zeroing_reports_failure():
    source = CountingSource(zero_ok=False)
    assert source.enter_safe_state() is False
    assert source.calls == ["set_voltage(0.0)", "turn_off"]


# --------------------------- SafeGuard / SourceGuard ---------------------------


class Outcome(Step):
    def __init__(self, outcome: Status | Exception) -> None:
        super().__init__(name="body")
        self.outcome = outcome

    def run(self) -> Status:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize("outcome", [Status.SUCCESS, Status.FAILED, RuntimeError("fault")])
def test_safe_guard_leaves_an_attenuator_dark_however_the_body_ends(outcome):
    attenuator = StandInAttenuator()
    attenuator.shutter_open, attenuator.attenuation_db = True, 3.0
    guard = SafeGuard(attenuator, Outcome(outcome))

    if isinstance(outcome, Exception):
        with pytest.raises(RuntimeError, match="fault"):
            ProcedureRunner().run(guard)
    else:
        assert ProcedureRunner().run(guard) is outcome
    assert attenuator.shutter_open is False
    assert attenuator.attenuation_db == attenuator.max_attenuation_db


def test_safe_guard_refuses_an_instrument_with_no_safe_state():
    with pytest.raises(TypeError, match="declares no safe state"):
        SafeGuard(StandInCounter(), Outcome(Status.SUCCESS))


def test_a_failed_safe_state_after_a_successful_body_is_an_error():
    source = CountingSource(zero_ok=False)
    with pytest.raises(RuntimeError, match="Could not enter the safe state"):
        ProcedureRunner().run(SafeGuard(source, Outcome(Status.SUCCESS)))


def test_source_guard_with_both_exit_flags_uses_the_declared_safe_state():
    source = CountingSource()
    ProcedureRunner().run(SourceGuard(source, Outcome(Status.SUCCESS)))
    assert source.calls == ["turn_on", "set_voltage(0.0)", "turn_off"]


def test_source_guard_honours_a_deliberately_partial_exit():
    """An IV curve can choose to leave the source biased-at-zero but on."""
    source = CountingSource()
    ProcedureRunner().run(SourceGuard(source, Outcome(Status.SUCCESS), turn_off_at_end=False))
    assert source.calls == ["turn_on", "set_voltage(0.0)"]
    assert source.output_enabled is True


# --------------------------- query declarations ---------------------------


@pytest.mark.parametrize(
    "cls",
    [VSense, Counter, Attenuator, YokoAttenuator, Attenuator31, FakeCounter.channel_class],
)
def test_every_declared_query_names_a_real_method(cls):
    """A typo in the allowlist would silently leave a real query counted as a write."""
    for name in collect_query_methods(cls):
        assert callable(getattr(cls, name, None)), f"{cls.__name__} declares missing query {name!r}"


def test_counting_is_not_a_query():
    """``count`` arms the counter for its input — a write another caller would feel."""
    queries = collect_query_methods(Counter)
    assert {"get_gate_time", "get_threshold"} <= queries
    assert not queries & {"count", "count_rate", "measure", "set_threshold"}


def test_queries_are_inherited_and_extended_never_removed():
    assert collect_query_methods(YokoAttenuator) == {
        "get_attenuation",
        "get_max_attenuation",
        "is_shutter_open",
        "get_wavelength_nm",
    }
    assert collect_query_methods(RemoteAttenuator) >= collect_query_methods(Attenuator)


def test_no_baseline_or_safe_state_is_ever_a_query():
    for cls in (VSource, VSense, Counter, Attenuator):
        queries = collect_query_methods(cls)
        assert "apply_baseline" not in queries and "enter_safe_state" not in queries


def test_apply_baseline_is_on_every_behavior():
    assert inspect.isfunction(InstrumentBehavior.apply_baseline)
