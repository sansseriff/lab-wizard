"""Driver tests for the Keysight 53220A, against a recording fake VISA dep.

The interesting behavior is the SCPI the driver emits, not the numbers it
parses: what a CONFigure destroys and therefore has to be re-applied, and what
it must not re-send on every point of a sweep.
"""

from __future__ import annotations

import pytest

from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.visa import VisaDep
from lab_wizard.lib.instruments.keysight53220A import (
    CounterTimeoutError,
    Keysight53220A,
    Keysight53220AChannelParams,
    Keysight53220AParams,
)


class FakeVisaDep(VisaDep):
    """Records every command and answers queries from a scripted table."""

    def __init__(self, answers: dict[str, str] | None = None) -> None:
        self.commands: list[str] = []
        self.answers = answers or {}
        self.timeouts: list[float] = []

    @property
    def is_open(self) -> bool:
        return True

    def write(self, cmd: str) -> None:
        self.commands.append(cmd)

    def read(self) -> str:
        raise AssertionError("the driver should only read through query()")

    def read_bytes(self, n: int) -> bytes:
        raise AssertionError("the driver takes no binary transfers")

    def query(self, cmd: str) -> str:
        self.commands.append(cmd)
        return self.answers.get(cmd, "+1.000000000000000E+003")

    def clear(self) -> None:
        pass

    def set_timeout(self, s: float) -> None:
        self.timeouts.append(s)

    def close(self) -> None:
        pass


def _counter(**overrides) -> tuple[Keysight53220A, FakeVisaDep]:
    params = Keysight53220AParams(
        ip_address="10.0.0.5",
        channels={0: Keysight53220AChannelParams(threshold_mV=-50.0, coupling="DC")},
        **overrides,
    )
    dep = FakeVisaDep()
    return Keysight53220A(dep, params), dep


def test_channels_are_counters_and_construction_is_silent() -> None:
    counter, dep = _counter()

    assert len(counter.channels) == 2
    assert all(isinstance(channel, Counter) for channel in counter)
    # Building the object must not reprogram hardware someone else may be using.
    assert dep.commands == []


def test_count_configures_then_restores_input_conditioning() -> None:
    counter, dep = _counter()

    counter[0].count(0.5)

    assert "CONF:TOT:TIM 0.5,(@1)" in dep.commands
    # CONFigure re-enables auto-level, so the threshold has to follow it.
    assert dep.commands.index("INP1:LEV -0.05") > dep.commands.index("CONF:TOT:TIM 0.5,(@1)")
    # ...and so does the trigger source, which CONFigure resets to IMMediate.
    assert dep.commands.index("TRIG:SOUR IMM") > dep.commands.index("CONF:TOT:TIM 0.5,(@1)")
    assert dep.commands[-1] == "READ?"


def test_repeated_counts_do_not_reconfigure() -> None:
    counter, dep = _counter()

    counter[0].count(0.5)
    configure_commands = dep.commands.count("CONF:TOT:TIM 0.5,(@1)")
    dep.commands.clear()

    counter[0].count(0.5)
    counter[0].count(0.5)

    assert configure_commands == 1
    assert dep.commands == ["READ?", "READ?"]


def test_switching_channel_rearms() -> None:
    counter, dep = _counter()

    counter[0].count(1.0)
    counter[1].count(1.0)

    assert "CONF:TOT:TIM 1.0,(@1)" in dep.commands
    assert "CONF:TOT:TIM 1.0,(@2)" in dep.commands
    assert "INP2:LEV -0.05" in dep.commands


def test_runtime_threshold_survives_the_next_configure() -> None:
    counter, dep = _counter()

    counter[0].count(1.0)
    counter[0].set_threshold(-32.5)
    dep.commands.clear()
    counter[0].count(0.25)  # a new gate time forces a fresh CONFigure

    assert "INP1:LEV -0.0325" in dep.commands
    assert counter[0].get_gate_time() == 0.25


def test_threshold_is_quantized_and_clamped_to_the_range() -> None:
    counter, dep = _counter()
    channel = counter[0]

    channel.set_threshold(-51.0)  # 2.5 mV grid on the 5 V range
    assert dep.commands[-1] == "INP1:LEV -0.05"

    channel.set_threshold(9000.0)  # beyond +/-5.125 V
    assert dep.commands[-1] == "INP1:LEV 5.125"


def test_ac_coupling_pushes_a_sub_step_threshold_off_zero() -> None:
    counter, dep = _counter()
    channel = counter[0]
    channel.set_coupling("AC")

    channel.set_threshold(0.0)

    assert dep.commands[-1] == "INP1:LEV 0.0025"


def test_get_threshold_falls_back_when_the_channel_is_not_armed() -> None:
    counter, dep = _counter()
    dep.answers["INP2:LEV?"] = "+9.91000000000000E+037"

    # Channel 2 is not the active measurement channel, so the counter answers
    # its sentinel rather than a level; the applied value is the honest answer.
    assert counter[1].get_threshold() == pytest.approx(-50.0)


def test_measurement_timeout_is_written_only_when_it_changes() -> None:
    counter, dep = _counter()

    counter[0].count(1.0)
    counter[0].count(1.0)
    counter[0].count(1.0)

    assert len([c for c in dep.commands if c.startswith("SYST:TIM")]) == 1


def test_long_gate_raises_both_timeouts() -> None:
    counter, dep = _counter()

    counter[0].count(60.0)

    assert "SYST:TIM 180.0" in dep.commands
    assert dep.timeouts[-1] == pytest.approx(180.0)


def test_external_trigger_sizes_the_visa_timeout_for_the_whole_cycle() -> None:
    counter, dep = _counter()
    counter.configure_trigger(source="external", trigger_count=2, sample_count=5)

    counter[0].count(1.0)

    assert "TRIG:SOUR EXT" in dep.commands
    assert "TRIG:COUN 2" in dep.commands
    assert "SAMP:COUN 5" in dep.commands
    assert dep.timeouts[-1] == pytest.approx(30.0)  # 3 x 1 s x 10 readings


def test_read_counts_keeps_the_readings_apart() -> None:
    counter, dep = _counter()
    dep.answers["READ?"] = "+1.0E+002,+2.0E+002,+3.0E+002"

    assert counter[0].read_counts(1.0) == [100, 200, 300]
    assert counter[0].count(1.0) == 600


def test_not_a_number_sentinel_is_not_reported_as_zero_counts() -> None:
    counter, dep = _counter()
    dep.answers["READ?"] = "+9.91000000000000E+037"

    with pytest.raises(CounterTimeoutError):
        counter[0].count(1.0)


def test_continuous_totalize_aborts_before_fetching() -> None:
    counter, dep = _counter()

    counter[0].start_totalize()
    counter[0].running_total()
    counter[0].stop_totalize()

    assert "CONF:TOT:CONT (@1)" in dep.commands
    assert "SENS:TOT:DATA?" in dep.commands
    assert dep.commands.index("ABOR") < dep.commands.index("FETC?")


def test_offline_counter_simulates_without_touching_the_transport() -> None:
    params = Keysight53220AParams(ip_address="10.0.0.5", offline=True)
    dep = FakeVisaDep()
    counter = Keysight53220A(dep, params)

    counts = counter[0].count(1.0)

    assert counts > 0
    assert dep.commands == []
    assert counter[0].get_threshold() == pytest.approx(-50.0)


def test_errors_drains_the_queue() -> None:
    counter, dep = _counter()
    replies = iter(
        ['-221,"Settings conflict"', '-113,"Undefined header"', '+0,"No error"']
    )

    def query(cmd: str) -> str:
        return next(replies) if cmd == "SYST:ERR?" else "0"

    dep.query = query  # type: ignore[method-assign]

    assert counter.errors() == ['-221,"Settings conflict"', '-113,"Undefined header"']
