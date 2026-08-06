"""
keysight53220A.py

Keysight 53220A universal counter — the count-rate readout for PCR
measurements. (The 53230A speaks the same SCPI for everything used here.)

Structure follows the library's channel convention:

  - :class:`Keysight53220AChannelParams` — per-input configuration
  - :class:`Keysight53220AParams`        — instrument params, ``CanInstantiate``
  - :class:`Keysight53220AChannel`       — one input, implements ``Counter``
  - :class:`Keysight53220A`              — the box, a ``ChannelProvider``

**Why the channels are not independent.** A 53220A has two front-panel inputs
but only one SCPI session, one reading memory, and — the part that shapes this
driver — one *measurement function* at a time. ``CONF:TOT:TIM (@1)`` does not
configure channel 1; it configures the instrument to totalize channel 1, and
the next ``CONF`` aimed at channel 2 takes that away. So a channel object holds
no transport of its own: it asks the instrument to arm the measurement it
wants, and the instrument tracks which one is currently armed and skips the
reprogramming when a sweep asks for the same thing again.

**Why settings get re-applied.** ``CONFigure`` and ``MEASure`` are documented to
re-enable auto-level and reset the threshold to 50% of peak-to-peak, and to
reset the trigger source to IMMediate. A driver that wrote the trigger level
once at connect time would silently lose it on the next measurement — which is
how a threshold sweep ends up flat. Every arming therefore re-applies the
channel's input conditioning and the instrument's trigger/gate settings from
``settings`` (below) rather than assuming the box remembers.

**``params`` vs ``settings``.** ``params`` is what the config asked for and is
left alone; ``settings`` is a deep copy holding what the hardware should be
right now. Runtime setters (``set_threshold``, ``set_coupling``, ...) update
``settings``, so the re-apply after a ``CONFigure`` restores the level a sweep
just set instead of reverting to YAML. :meth:`Keysight53220AChannel.restore_configured_settings`
goes back to the config values.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import ClassVar, Literal, NamedTuple

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.parent_child import (
    CanInstantiate,
    ChannelProvider,
    ChannelsLike,
    Instrument,
    IPLike,
)
from lab_wizard.lib.instruments.general.visa import LocalVisaDep, VisaDep
from lab_wizard.lib.utilities.model_tree import ResourceConfig

logger = logging.getLogger(__name__)


# The counter answers 9.91E+37 for "no reading" — a measurement that timed out,
# or a threshold queried on a channel that is not the active measurement
# channel. Anything at or above this floor is that sentinel, not data.
NOT_A_NUMBER_FLOOR = 9.9e37

# SYSTem:TIMeout accepts 10 ms to 2000 s.
MEASUREMENT_TIMEOUT_RANGE_S = (0.010, 2000.0)

# Threshold resolution is the input range divided by this: 2.5 mV on the 5 V
# range, 25 mV on 50 V, 250 mV on 500 V. Absolute levels reach 1.025x range.
THRESHOLD_STEPS_PER_RANGE = 2000.0
THRESHOLD_RANGE_MARGIN = 1.025

# Count rate the offline simulator reports, before shot noise.
OFFLINE_COUNT_RATE_HZ = 10_000.0

_SCPI_EDGE: dict[str, str] = {"positive": "POS", "negative": "NEG"}
_SCPI_TRIGGER_SOURCE: dict[str, str] = {
    "immediate": "IMM",
    "external": "EXT",
    "bus": "BUS",
}
_SCPI_GATE_SOURCE: dict[str, str] = {
    "time": "TIME",
    "external": "EXT",
    "input1": "INP1",
    "input2": "INP2",
}


class CounterTimeoutError(RuntimeError):
    """A gate did not complete before the counter's measurement timeout.

    Raised rather than returning zero: a gate that never closed produced no
    count, and a zero here would enter the data set as a real measurement of
    no photons.
    """


class SignalLevels(NamedTuple):
    """Peak levels of the signal at an input, in millivolts."""

    minimum_mV: float
    maximum_mV: float
    peak_to_peak_mV: float


@dataclass(frozen=True)
class _MeasurementSetup:
    """The measurement the instrument is currently armed for."""

    function: Literal["totalize_timed", "totalize_continuous", "frequency"]
    scpi_channel: int
    gate_time_s: float


class Keysight53220AChannelParams(BaseModel):
    """Input conditioning and gating for one counter input.

    The defaults are an SNSPD readout: 50 Ω terminated, DC coupled, counting
    rising edges through a -50 mV discriminator.
    """

    attribute_name: str = ""
    gate_time_s: float = Field(
        default=1.0,
        description="(s) length of one gated count",
    )
    threshold_mode: Literal["absolute", "relative", "auto"] = Field(
        default="absolute",
        description="absolute mV / percent of peak-to-peak / auto-level at 50%",
    )
    threshold_mV: float = Field(
        default=-50.0,
        description="(mV) discriminator level when threshold_mode is absolute",
    )
    threshold_percent: float = Field(
        default=50.0,
        description="(%) of peak-to-peak when threshold_mode is relative (10-90, 5% steps)",
    )
    slope: Literal["positive", "negative"] = Field(
        default="positive",
        description="edge a pulse is counted on",
    )
    coupling: Literal["AC", "DC"] = Field(
        default="DC",
        description="AC removes the DC content; DC reaches down to 1 mHz",
    )
    impedance_ohm: Literal[50, 1000000] = Field(
        default=50,
        description="(ohm) 50 terminates a coax run; 1000000 bridges",
    )
    input_range_V: float = Field(
        default=5.0,
        description="(V) signal operating range: 5 or 50 with a 1:1 probe",
    )
    probe_factor: Literal[1, 10] = Field(
        default=1,
        description="1 for a 1:1 probe, 10 for a 10:1 probe",
    )
    noise_rejection: bool = Field(
        default=False,
        description="Widen hysteresis: rejects noise, halves sensitivity",
    )
    lowpass_filter: bool = Field(
        default=False,
        description="Insert the 100 kHz low-pass filter",
    )


class Keysight53220AChannel(Counter):
    """One input of a 53220A, as a :class:`Counter`.

    Constructing this touches no hardware — the instrument builds one object
    per physical input whether or not anyone uses it, so construction has to
    stay free. Settings reach the hardware when a measurement is armed, or on
    an explicit :meth:`apply_input_settings`.
    """

    def __init__(
        self,
        instrument: "Keysight53220A",
        channel_index: int,
        params: Keysight53220AChannelParams,
    ) -> None:
        self.instrument = instrument
        self.channel_index = channel_index
        self.scpi_channel = channel_index + 1  # hardware inputs are 1-based
        self.attribute_name = params.attribute_name
        self.params = params
        self.settings = params.model_copy(deep=True)

    def __str__(self) -> str:
        return f"Keysight53220A channel {self.scpi_channel}"

    @property
    def offline(self) -> bool:
        return self.instrument.offline

    # ---- Counter contract --------------------------------------------------

    def count(self, gate_time: float | None = None) -> int:
        """Totalize edges on this input for one gate and return the count.

        With the default trigger configuration the counter takes one reading,
        and this is that reading. Under a multi-reading trigger configuration
        (``trigger_count`` x ``sample_count``) the readings are summed, so the
        answer stays "events counted"; :meth:`read_counts` keeps them apart.
        """
        return int(sum(self.read_counts(gate_time)))

    def set_gate_time(self, gate_time: float) -> bool:
        """Set the gate length for subsequent counts.

        Deliberately no I/O: the gate time is an argument of the ``CONFigure``
        that arms the next measurement, so writing it now would be overwritten
        moments later.
        """
        self.settings.gate_time_s = float(gate_time)
        return True

    def get_gate_time(self) -> float:
        return self.settings.gate_time_s

    def set_threshold(self, threshold_mV: float) -> bool:
        """Set the absolute discriminator level, in millivolts.

        Setting an absolute level turns off auto-leveling, which is what makes
        a threshold sweep repeatable. The value is quantized and clamped to the
        current input range first, so :meth:`get_threshold` and the sweep axis
        agree with the hardware.
        """
        volts = self._conditioned_threshold_v(threshold_mV / 1000.0)
        self.settings.threshold_mode = "absolute"
        self.settings.threshold_mV = volts * 1000.0
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:LEV {volts}")
        return True

    def get_threshold(self) -> float:
        """Threshold in millivolts, read back from the counter.

        The counter answers the not-a-number sentinel for an input that is not
        the active measurement channel, in which case the level this driver
        last applied is the honest answer and is returned instead.
        """
        if self.offline:
            return self.settings.threshold_mV
        raw = _parse_float(self.instrument.query(f"INP{self.scpi_channel}:LEV?"))
        if math.isnan(raw) or abs(raw) >= NOT_A_NUMBER_FLOOR:
            logger.debug(
                "Channel %d is not the active measurement channel; "
                "reporting the applied threshold instead of the sentinel.",
                self.scpi_channel,
            )
            return self.settings.threshold_mV
        return raw * 1000.0

    # ---- Reading counts ----------------------------------------------------

    def read_counts(self, gate_time: float | None = None) -> list[int]:
        """Run one full trigger cycle and return every gated count separately.

        The list holds ``trigger_count * sample_count`` readings, in order.
        This is the shape a pulsed experiment wants: with the source gated to
        the first sample of each trigger, ``readings[0]`` is the illuminated
        count and the rest are dark.
        """
        gate = self.settings.gate_time_s if gate_time is None else float(gate_time)
        self.settings.gate_time_s = gate

        if self.offline:
            return self._simulated_counts(gate)

        self.instrument.arm(_MeasurementSetup("totalize_timed", self.scpi_channel, gate), self)
        readings = self.instrument.read_measurements()
        return [int(round(value)) for value in readings]

    def measure_frequency(self, gate_time: float | None = None) -> float:
        """Measure frequency on this input in Hz.

        The counter's own frequency function, as distinct from
        ``count_rate()`` which divides a totalized count by the gate. Use this
        for a periodic signal (a clock, a pulse generator); use ``count_rate``
        for random arrivals such as detector clicks, where "frequency" of an
        aperiodic train is not a well-defined quantity.
        """
        gate = self.settings.gate_time_s if gate_time is None else float(gate_time)

        if self.offline:
            return OFFLINE_COUNT_RATE_HZ

        self.instrument.arm(_MeasurementSetup("frequency", self.scpi_channel, gate), self)
        readings = self.instrument.read_measurements()
        return readings[0] if readings else math.nan

    # ---- Continuous totalizing --------------------------------------------

    def start_totalize(self) -> bool:
        """Begin counting indefinitely, with no gate.

        Pairs with :meth:`running_total` (read while counting) and
        :meth:`stop_totalize`. Useful for aligning an optical setup, where you
        want the count to keep climbing while you turn a knob.
        """
        if self.offline:
            return True
        self.instrument.arm(
            _MeasurementSetup("totalize_continuous", self.scpi_channel, math.inf), self
        )
        self.instrument.initiate()
        return True

    def running_total(self) -> int:
        """Count accumulated so far, without stopping the measurement."""
        if self.offline:
            return self._simulated_counts(1.0)[0]
        return int(round(_parse_float(self.instrument.query("SENS:TOT:DATA?"))))

    def stop_totalize(self) -> int:
        """Stop counting and return the final total.

        The count has to be fetched after the abort — the counter refuses to
        hand over a continuous total while the gate is still open.
        """
        if self.offline:
            return self._simulated_counts(1.0)[0]
        self.instrument.abort()
        readings = self.instrument.fetch_measurements()
        return int(round(sum(readings)))

    # ---- Input conditioning ------------------------------------------------

    def apply_input_settings(self) -> bool:
        """Write this input's whole conditioning chain to the hardware.

        Ordered impedance -> probe -> range -> coupling -> filter -> noise
        rejection -> slope -> threshold, because the threshold's resolution and
        limits are defined by the range and probe factor ahead of it. Called
        automatically whenever the instrument arms a measurement, since
        ``CONFigure`` discards the threshold.
        """
        if self.offline:
            return True

        settings = self.settings
        channel = self.scpi_channel
        write = self.instrument.write

        write(f"INP{channel}:IMP {float(settings.impedance_ohm)}")
        write(f"INP{channel}:PROB {settings.probe_factor}")
        write(f"INP{channel}:RANG {settings.input_range_V}")
        write(f"INP{channel}:COUP {settings.coupling}")
        write(f"INP{channel}:FILT {_on_off(settings.lowpass_filter)}")
        write(f"INP{channel}:NREJ {_on_off(settings.noise_rejection)}")
        write(f"INP{channel}:SLOP {_SCPI_EDGE[settings.slope]}")

        if settings.threshold_mode == "absolute":
            volts = self._conditioned_threshold_v(settings.threshold_mV / 1000.0)
            settings.threshold_mV = volts * 1000.0
            write(f"INP{channel}:LEV {volts}")
        elif settings.threshold_mode == "relative":
            write(f"INP{channel}:LEV:AUTO ON")
            write(f"INP{channel}:LEV:REL {settings.threshold_percent}")
        else:  # "auto" — CONFigure's own default, stated rather than assumed
            write(f"INP{channel}:LEV:AUTO ON")
        return True

    def restore_configured_settings(self) -> bool:
        """Discard runtime changes and go back to the configured params."""
        self.settings = self.params.model_copy(deep=True)
        return self.apply_input_settings()

    def set_coupling(self, coupling: Literal["AC", "DC"]) -> bool:
        self.settings.coupling = coupling
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:COUP {coupling}")
        return True

    def set_impedance(self, impedance_ohm: Literal[50, 1000000]) -> bool:
        self.settings.impedance_ohm = impedance_ohm
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:IMP {float(impedance_ohm)}")
        return True

    def set_slope(self, slope: Literal["positive", "negative"]) -> bool:
        """Choose the edge a pulse is counted on."""
        self.settings.slope = slope
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:SLOP {_SCPI_EDGE[slope]}")
        return True

    def set_noise_rejection(self, enabled: bool) -> bool:
        """Widen the hysteresis band by 2x.

        Worth having when the signal environment is noisy, at the cost of half
        the sensitivity: a threshold sitting near a peak may stop counting
        entirely, because the pulse no longer crosses both hysteresis levels.
        """
        self.settings.noise_rejection = enabled
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:NREJ {_on_off(enabled)}")
        return True

    def set_lowpass_filter(self, enabled: bool) -> bool:
        """Switch the 100 kHz low-pass filter into the input path."""
        self.settings.lowpass_filter = enabled
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:FILT {_on_off(enabled)}")
        return True

    def set_input_range(self, input_range_V: float) -> bool:
        """Set the signal operating range (5 or 50 V with a 1:1 probe).

        The range sets threshold resolution, so the applied threshold is
        re-quantized onto the new grid.
        """
        self.settings.input_range_V = float(input_range_V)
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:RANG {self.settings.input_range_V}")
        if self.settings.threshold_mode == "absolute":
            return self.set_threshold(self.settings.threshold_mV)
        return True

    def set_probe_factor(self, probe_factor: Literal[1, 10]) -> bool:
        """Tell the counter what probe is attached, so levels read as DUT levels."""
        self.settings.probe_factor = probe_factor
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:PROB {probe_factor}")
        return True

    def set_threshold_relative(self, percent: float) -> bool:
        """Set the threshold as a percentage of peak-to-peak (10-90%, 5% steps).

        Requires auto-level, which this enables. Handy for a signal of unknown
        amplitude; unsuitable below 50 Hz, and unsuitable for detector clicks,
        whose peak-to-peak the counter cannot track between sparse pulses —
        use :meth:`set_threshold` there.
        """
        self.settings.threshold_mode = "relative"
        self.settings.threshold_percent = float(percent)
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:LEV:AUTO ON")
            self.instrument.write(f"INP{self.scpi_channel}:LEV:REL {float(percent)}")
        return True

    def set_auto_level(self, mode: Literal["off", "on", "once"]) -> bool:
        """Enable, disable, or take a single auto-level from the input peaks."""
        self.settings.threshold_mode = "auto" if mode != "off" else "absolute"
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:LEV:AUTO {mode.upper()}")
        return True

    def signal_levels(self) -> SignalLevels:
        """Measure the input's min, max, and peak-to-peak levels in millivolts.

        What a threshold sweep needs to pick its range: the useful thresholds
        lie between the minimum and maximum of the pulse train, and asking the
        counter beats guessing from a datasheet.
        """
        if self.offline:
            return SignalLevels(-100.0, 100.0, 200.0)
        channel = self.scpi_channel
        minimum = _parse_float(self.instrument.query(f"INP{channel}:LEV:MIN?"))
        maximum = _parse_float(self.instrument.query(f"INP{channel}:LEV:MAX?"))
        peak_to_peak = _parse_float(self.instrument.query(f"INP{channel}:LEV:PTP?"))
        return SignalLevels(minimum * 1000.0, maximum * 1000.0, peak_to_peak * 1000.0)

    # ---- Input protection --------------------------------------------------

    def protection_tripped(self) -> bool:
        """Whether the input protection relay has opened.

        Above roughly ±10 V the counter drops the 50 Ω termination to save the
        input — and keeps *displaying* 50 Ω. Counts collected after that are
        taken through the wrong termination, so a run that cares about
        amplitude should check this.
        """
        if self.offline:
            return False
        return _parse_float(self.instrument.query(f"INP{self.scpi_channel}:PROT?")) >= 0.5

    def clear_protection(self) -> bool:
        """Close the protection relay again, restoring 50 Ω.

        Only do this once the overvoltage is gone; the relay will just reopen.
        """
        if not self.offline:
            self.instrument.write(f"INP{self.scpi_channel}:PROT:CLE")
        return True

    # ---- Internals ---------------------------------------------------------

    def _conditioned_threshold_v(self, volts: float) -> float:
        """Quantize and clamp a threshold the way the hardware will.

        Two corrections, both of which change what the instrument counts:

        * The counter rounds to its threshold resolution (range / 2000), so
          the value asked for and the value used differ by up to half a step.
        * With AC coupling, a level below one step rounds to 0 V, which sits
          in the middle of an AC-coupled signal's noise and triggers on it.
          Such a level is pushed out to the first real step instead, keeping
          the sign the caller asked for.
        """
        resolution = self.settings.input_range_V / THRESHOLD_STEPS_PER_RANGE
        limit = self.settings.input_range_V * THRESHOLD_RANGE_MARGIN

        quantized = round(volts / resolution) * resolution
        if self.settings.coupling == "AC" and abs(quantized) < resolution:
            quantized = -resolution if volts < 0 else resolution
        if abs(quantized) > limit:
            logger.warning(
                "Threshold %.4f V exceeds the +/-%.4f V limit of the %g V range; clamping.",
                quantized,
                limit,
                self.settings.input_range_V,
            )
            quantized = math.copysign(limit, quantized)
        return quantized

    def _simulated_counts(self, gate_time: float) -> list[int]:
        """Offline stand-in: a steady rate with Poissonian shot noise.

        Gaussian about the mean rather than a true Poisson draw — at the rates
        this simulates the two are indistinguishable, and the point is only
        that repeated points scatter like counts instead of repeating exactly.
        """
        settings = self.instrument.settings
        readings = max(1, settings.trigger_count * settings.sample_count)
        mean = OFFLINE_COUNT_RATE_HZ * (gate_time if math.isfinite(gate_time) else 1.0)
        return [max(0, int(random.gauss(mean, math.sqrt(mean)))) for _ in range(readings)]


class Keysight53220AParams(ChannelsLike, IPLike, BaseModel, CanInstantiate["Keysight53220A"]):
    """Parameters for the Keysight 53220A universal counter.

    A standalone top-level instrument reached over TCP/IP. Per-input settings
    live in ``channels``, keyed by hardware channel index; the trigger and gate
    settings here belong to the box, since one trigger cycle drives whichever
    input is armed.
    """

    type: Literal["keysight53220A"] = "keysight53220A"
    ip_address: str = "10.7.0.114"
    ip_port: int = 5025
    offline: bool = False
    visa_timeout_s: float = Field(
        default=10.0,
        description="(s) floor for the VISA read timeout; long gates raise it automatically",
    )
    measurement_timeout_s: float = Field(
        default=10.0,
        description="(s) floor for the counter's own per-measurement timeout (0.01-2000)",
    )
    trigger_source: Literal["immediate", "external", "bus"] = Field(
        default="immediate",
        description="immediate free-runs; external waits on the rear-panel Trig In",
    )
    trigger_slope: Literal["positive", "negative"] = Field(
        default="positive",
        description="edge of the external trigger that starts a cycle",
    )
    trigger_delay_s: float = Field(
        default=0.0,
        description="(s) delay between the trigger and the first gate",
    )
    trigger_count: int = Field(
        default=1,
        description="triggers accepted before the counter goes idle",
    )
    sample_count: int = Field(
        default=1,
        description="readings taken per trigger",
    )
    gate_source: Literal["time", "external", "input1", "input2"] = Field(
        default="time",
        description="time gates for gate_time_s; the others gate on an external signal",
    )
    gate_polarity: Literal["positive", "negative"] = Field(
        default="positive",
        description="edge that opens an external gate (the opposite edge closes it)",
    )
    num_channels: ClassVar[int] = 2
    channels: dict[int, Keysight53220AChannelParams] = Field(default_factory=dict)

    @classmethod
    def resource_class(cls) -> type[Keysight53220A]:
        return Keysight53220A

    def create_inst(self) -> Keysight53220A:
        return Keysight53220A.from_params(self)

    # -- Transport ----------------------------------------------------------
    # Raw SCPI socket: the instrument accepts a single session on this port, so
    # a second process is refused by the hardware rather than multiplexed.

    def transport_key(self) -> str | None:
        return f"visa-tcp://{self.ip_address}:{self.ip_port}"


class Keysight53220A(Instrument, ChannelProvider[Keysight53220AChannel]):
    """Keysight 53220A universal counter.

    Channels are reached through the ``ChannelProvider`` interface —
    ``counter[0].count(1.0)``, ``counter[1].set_threshold(-30)``.

    The constructor opens nothing and writes nothing: the VISA dep connects on
    first use, and hardware settings are written when a measurement is armed
    (or on an explicit :meth:`apply_configuration`). A driver that reprogrammed
    the counter merely because someone built the object would disturb a
    measurement already running on the bench.
    """

    channel_class = Keysight53220AChannel

    def __init__(self, dep: VisaDep, params: Keysight53220AParams):
        self._dep = dep
        self.params = params
        self.settings = params.model_copy(deep=True)
        self.offline = params.offline

        # Which measurement the box is armed for, and the last timeout written.
        # Both are caches over hardware state, dropped whenever we lose track.
        self._armed: _MeasurementSetup | None = None
        self._measurement_timeout_s: float | None = None

        self.channels: list[Keysight53220AChannel] = [
            Keysight53220AChannel(
                self, index, params.channels.get(index, Keysight53220AChannelParams())
            )
            for index in range(params.num_channels)
        ]

    def __str__(self) -> str:
        return f"Keysight53220A ({self.params.ip_address}): {len(self.channels)} channels"

    @classmethod
    def from_params(cls, params: Keysight53220AParams) -> Keysight53220A:
        resource = f"TCPIP::{params.ip_address}::{params.ip_port}::SOCKET"
        dep = LocalVisaDep(
            resource=resource,
            timeout=params.visa_timeout_s,
            # A raw socket carries no message boundaries of its own.
            read_termination="\n",
            write_termination="\n",
        )
        return cls(dep, params)

    @classmethod
    def from_config(cls, resources: ResourceConfig, *, key: str) -> Keysight53220A:
        raw = resources.instruments[key]
        if not isinstance(raw, Keysight53220AParams):
            raise TypeError(
                f"Expected Keysight53220AParams at resources.instruments[{key!r}]"
            )
        return cls.from_params(raw)

    # ---- Raw SCPI ----------------------------------------------------------

    def write(self, command: str) -> None:
        """Send a SCPI command. The escape hatch for anything not wrapped here."""
        if self.offline:
            logger.debug("offline, not sending: %s", command)
            return
        self._dep.write(command)

    def query(self, command: str) -> str:
        """Send a SCPI query and return the reply."""
        if self.offline:
            raise RuntimeError(
                f"Counter is offline; the query {command!r} has no answer. "
                "Offline callers should use the driver's methods, which "
                "simulate rather than ask the hardware."
            )
        return self._dep.query(command).strip()

    # ---- Identity and housekeeping ----------------------------------------

    def identity(self) -> str:
        """``*IDN?`` — manufacturer, model, serial, firmware."""
        if self.offline:
            return "Keysight,53220A,OFFLINE,0.0"
        return self.query("*IDN?")

    def reset(self) -> bool:
        """``*RST``: factory defaults, idle state, measurement aborted."""
        self.write("*RST")
        self._forget_hardware_state()
        return True

    def preset(self) -> bool:
        """``SYSTem:PRESet``: like a front-panel Preset, keeping I/O settings."""
        self.write("SYST:PRES")
        self._forget_hardware_state()
        return True

    def clear_status(self) -> bool:
        """``*CLS``: clear the status registers and the error queue."""
        self.write("*CLS")
        return True

    def self_test(self) -> bool:
        """``*TST?`` — True if the counter's self-test passes (takes seconds)."""
        if self.offline:
            return True
        return _parse_float(self.query("*TST?")) == 0.0

    def errors(self) -> list[str]:
        """Drain the counter's error queue, oldest first.

        Worth calling after a configuration change: SCPI reports a rejected
        setting here rather than by failing the write, so a settings conflict
        is otherwise invisible until the numbers look wrong.
        """
        if self.offline:
            return []
        messages: list[str] = []
        for _ in range(32):  # the queue holds 20; the bound stops a stuck loop
            reply = self.query("SYST:ERR?")
            if not reply or reply.startswith(("+0,", "0,")):
                break
            messages.append(reply)
        return messages

    # ---- Measurement cycle -------------------------------------------------

    def initiate(self) -> bool:
        """``INITiate``: leave idle and wait for the trigger."""
        self.write("INIT")
        return True

    def abort(self) -> bool:
        """``ABORt``: stop the measurement in progress and return to idle."""
        self.write("ABOR")
        return True

    def read_measurements(self) -> list[float]:
        """``READ?``: initiate, wait for the whole trigger cycle, return readings."""
        return self._parse_readings(self.query("READ?"))

    def fetch_measurements(self) -> list[float]:
        """``FETCh?``: readings already in memory, without re-measuring."""
        return self._parse_readings(self.query("FETC?"))

    def readings_available(self) -> int:
        """``DATA:POINts?``: readings sitting in memory right now."""
        if self.offline:
            return 0
        return int(_parse_float(self.query("DATA:POIN?")))

    # ---- Configuration -----------------------------------------------------

    def apply_configuration(self) -> bool:
        """Push the trigger, gate, and every channel's conditioning to hardware.

        Not needed before measuring — arming does this — but useful to make
        the front panel show what the config says, and to surface a bad
        setting through :meth:`errors` before a run starts.
        """
        self.apply_trigger_settings()
        for channel in self.channels:
            channel.apply_input_settings()
        return True

    def configure_trigger(
        self,
        *,
        source: Literal["immediate", "external", "bus"] | None = None,
        slope: Literal["positive", "negative"] | None = None,
        delay_s: float | None = None,
        trigger_count: int | None = None,
        sample_count: int | None = None,
    ) -> bool:
        """Set the system trigger; only the arguments given are changed.

        The counter takes ``trigger_count * sample_count`` readings per cycle
        and then goes idle. The classic pulsed-source arrangement is an
        external trigger from the source with several samples per trigger: the
        first sample sees the light, the rest measure dark counts in the same
        run.
        """
        if source is not None:
            self.settings.trigger_source = source
        if slope is not None:
            self.settings.trigger_slope = slope
        if delay_s is not None:
            self.settings.trigger_delay_s = float(delay_s)
        if trigger_count is not None:
            self.settings.trigger_count = int(trigger_count)
        if sample_count is not None:
            self.settings.sample_count = int(sample_count)
        return self.apply_trigger_settings()

    def apply_trigger_settings(self) -> bool:
        """Write the trigger settings, and size the timeouts to match them."""
        settings = self.settings
        self.write(f"TRIG:SOUR {_SCPI_TRIGGER_SOURCE[settings.trigger_source]}")
        self.write(f"TRIG:SLOP {_SCPI_EDGE[settings.trigger_slope]}")
        self.write(f"TRIG:DEL {settings.trigger_delay_s}")
        self.write(f"TRIG:COUN {settings.trigger_count}")
        self.write(f"SAMP:COUN {settings.sample_count}")
        return True

    def configure_gate(
        self,
        *,
        source: Literal["time", "external", "input1", "input2"] | None = None,
        polarity: Literal["positive", "negative"] | None = None,
    ) -> bool:
        """Choose what opens and closes the totalize gate.

        ``time`` gates for the channel's gate time. The others gate on a
        signal — the rear-panel Gate In/Out BNC, or the other input — which is
        how a count is made to cover exactly a laser pulse. An input used as
        the gate cannot also be the input being counted.
        """
        if source is not None:
            self.settings.gate_source = source
        if polarity is not None:
            self.settings.gate_polarity = polarity
        self._armed = None  # the gate is part of the armed setup
        return True

    def set_measurement_timeout(self, seconds: float) -> bool:
        """Set how long the counter waits for one gate before giving up.

        Written only when it changes: ``SYSTem:TIMeout`` is stored in
        non-volatile memory, and rewriting it on every point of a sweep is
        thousands of pointless flash writes.
        """
        low, high = MEASUREMENT_TIMEOUT_RANGE_S
        clamped = min(max(seconds, low), high)
        if self._measurement_timeout_s is not None and math.isclose(
            clamped, self._measurement_timeout_s, rel_tol=1e-3
        ):
            return True
        self.write(f"SYST:TIM {clamped}")
        self._measurement_timeout_s = clamped
        return True

    # ---- Arming ------------------------------------------------------------

    def arm(self, setup: _MeasurementSetup, channel: Keysight53220AChannel) -> bool:
        """Make the counter ready for ``setup``, reprogramming only if needed.

        A bias sweep asks for the same timed totalize at every point, so the
        repeat case has to be free — otherwise each point pays for a
        ``CONFigure`` and a full input reprogram, and at 10 ms gates that
        dominates the run.

        When the setup does change, order matters: ``CONFigure`` first, then
        trigger, gate, and input conditioning, because ``CONFigure`` resets the
        trigger source to IMMediate and re-enables auto-level. Doing it the
        other way round is the classic way to lose a trigger level.
        """
        self._size_timeouts(setup)
        if setup == self._armed:
            return True

        if setup.function == "totalize_timed":
            self.write(f"CONF:TOT:TIM {setup.gate_time_s},(@{setup.scpi_channel})")
        elif setup.function == "totalize_continuous":
            self.write(f"CONF:TOT:CONT (@{setup.scpi_channel})")
        else:  # frequency
            self.write(f"CONF:FREQ (@{setup.scpi_channel})")
            self.write(f"SENS:FREQ:GATE:TIME {setup.gate_time_s}")

        self.apply_trigger_settings()
        self._apply_gate_settings(setup)
        channel.apply_input_settings()
        self._armed = setup
        return True

    def _apply_gate_settings(self, setup: _MeasurementSetup) -> bool:
        """Write the gate source and polarity for the function just configured.

        Set last of the gate parameters, per the manual: selecting the source
        before the parameters it depends on is what produces "settings
        conflict" errors. Continuous totalizing has its gate fixed by
        ``CONF:TOT:CONT`` and is left alone.
        """
        if setup.function == "totalize_continuous":
            return True
        subsystem = "TOT" if setup.function.startswith("totalize") else "FREQ"
        settings = self.settings
        self.write(f"SENS:{subsystem}:GATE:POL {_SCPI_EDGE[settings.gate_polarity]}")
        self.write(f"SENS:{subsystem}:GATE:SOUR {_SCPI_GATE_SOURCE[settings.gate_source]}")
        return True

    def _size_timeouts(self, setup: _MeasurementSetup) -> None:
        """Give both timeouts room for the cycle this setup will run.

        Two separate clocks have to be long enough or the measurement fails in
        two different ways: the counter's own per-gate timeout (which returns
        the not-a-number sentinel), and the VISA read timeout on ``READ?``
        (which raises). Both get 3x the expected time — enough for arming and
        transfer overhead without waiting forever on a stuck acquisition.
        """
        gate = setup.gate_time_s if math.isfinite(setup.gate_time_s) else 0.0
        settings = self.settings
        per_reading = gate + settings.trigger_delay_s
        readings = max(1, settings.trigger_count * settings.sample_count)

        self.set_measurement_timeout(max(settings.measurement_timeout_s, 3.0 * per_reading))
        if not self.offline:
            self._dep.set_timeout(max(settings.visa_timeout_s, 3.0 * per_reading * readings))

    def _forget_hardware_state(self) -> None:
        """Drop the caches after anything that changes the box underneath them."""
        self._armed = None
        self._measurement_timeout_s = None

    def _parse_readings(self, payload: str) -> list[float]:
        """Turn an ASCII reading block into floats, refusing unusable readings."""
        readings = [_parse_float(item) for item in payload.split(",") if item.strip()]
        if any(math.isnan(value) or abs(value) >= NOT_A_NUMBER_FLOOR for value in readings):
            raise CounterTimeoutError(
                f"The counter returned no usable reading ({payload!r}). Its "
                "not-a-number sentinel means a gate did not complete within the "
                f"{self._measurement_timeout_s} s measurement timeout: raise "
                "measurement_timeout_s, or check that the signal and the gate "
                "source are actually present."
            )
        return readings


def _on_off(enabled: bool) -> str:
    return "ON" if enabled else "OFF"


def _parse_float(payload: str) -> float:
    """Parse one SCPI number, answering NaN for a reply that is not one."""
    try:
        return float(str(payload).strip().split(",")[0])
    except (TypeError, ValueError):
        logger.warning("Could not parse a number from counter reply %r", payload)
        return math.nan
