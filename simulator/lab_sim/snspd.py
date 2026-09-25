"""A one-detector SNSPD circuit model — the physics the simulated bench reports.

The simulated instruments speak their real wire protocols; this module is what
gives them something true to say back. Nothing here knows about serial ports,
GPIB, or sockets: it is a pure circuit so it can be reasoned about and tested
on its own.

The circuit is the usual SNSPD bias arrangement::

    V_bias ──[ R_bias ]──┬── SNSPD ── gnd
                         │
                      voltmeter

A voltage source drives a series resistor, which turns
an applied voltage into a nearly ideal current source, and a voltmeter reads
the drop across the detector itself.

Two branches, and which one you are on depends on history:

**superconducting** — the detector is a short. It drops no voltage at all, so
the voltmeter reads zero and the current is ``V_bias / R_bias``. This holds
until the current reaches the switching current ``I_c``, at which point the
superconductivity breaks.

**normal (latched)** — a normal-conducting hotspot of resistance ``R_n`` sits
in series with the bias resistor, so the current drops to
``V_bias / (R_bias + R_n)`` and the voltmeter reads ``I · R_n``: a nonzero
voltage that rises monotonically with bias. It stays latched until the current
falls below the retrapping current ``I_r``, which is *lower* than ``I_c`` —
that gap is why a real IV curve is hysteretic, and why sweeping back down does
not retrace the way up.

Latching requires ``I_r < I_c · R_bias / (R_bias + R_n)``; otherwise the device
retraps the instant it switches and the model oscillates between branches on
successive reads, exactly as real relaxation oscillations do. The defaults are
comfortably inside the latching regime.

The resistor defaults to 100 kΩ, matching the IV procedure's default
``bias_resistance_ohm``, so the default detector switches at 0.03 V applied.

**Photon counting.** The same detector also reports what a counter wired to its
output would see, which is what makes a simulated PCR curve possible. Detection
efficiency against bias current is an error function::

    eta(I) = eta_max · ½·(1 + erf((I - I_mid) / (√2 · w)))

— the standard shape, and for the standard reason: a photon absorbed anywhere
along the wire triggers a click only if the local current is high enough, and
the spread of those local thresholds is roughly Gaussian, so the *cumulative*
probability is its integral. ``I_mid`` is the bias at half the plateau and
``w`` sets how sharp the turn-on is. A detector with internal saturation has a
small ``w`` and a long flat plateau; one without never quite flattens.

Two things bend that curve at the ends, and both are what a real PCR curve
looks like rather than decoration:

* **Dark counts** double every ``dark_count_doubling_current_a`` of bias, so
  they are invisible under the plateau and then take over near ``I_c``. That
  crossover is the reason a PCR curve is measured at all.
* **Latching** ends the curve. Above ``I_c`` the detector sits normal, emits no
  pulses, and the count rate falls to zero rather than continuing to rise.

The counter's discriminator is modelled too, since a threshold sweep is the
other thing this detector is asked for: counts pass while the threshold is
below the pulse amplitude and roll off across ``pulse_amplitude_spread_mV``
around it. The comparison is on magnitude, so the model takes no position on
whether the readout chain inverts.
"""

from __future__ import annotations

import math
import random

from pydantic import BaseModel, Field


# Above this mean, a Poisson draw is indistinguishable from a Gaussian one and
# the direct method costs a multiply per event.
_POISSON_GAUSSIAN_THRESHOLD = 30.0


class SnspdParams(BaseModel):
    """Device and circuit constants for one simulated detector."""

    bias_resistance_ohm: float = Field(
        default=1.0e5,
        description="(ohm) series resistor between the voltage source and the detector",
    )
    critical_current_a: float = Field(
        default=3.0e-7,
        description="(A) bias current at which superconductivity breaks",
    )
    retrapping_current_a: float = Field(
        default=1.5e-7,
        description="(A) bias current at which the hotspot re-cools; must be below the critical current",
    )
    normal_resistance_ohm: float = Field(
        default=5.0e4,
        description="(ohm) resistance of the latched hotspot",
    )
    noise_volts: float = Field(
        default=0.0,
        description="(V) gaussian noise added to every voltmeter reading; 0 makes runs deterministic",
    )
    seed: int = Field(
        default=20250805,
        description="Seed for the noise generator, so a noisy run is still reproducible",
    )

    # -- photon counting ----------------------------------------------------

    incident_photon_rate_hz: float = Field(
        default=1.0e6,
        description="(Hz) photons arriving at the detector",
    )
    max_detection_efficiency: float = Field(
        default=0.8,
        description="Fraction of incident photons counted on the plateau (0-1)",
    )
    detection_midpoint_current_a: float = Field(
        default=2.0e-7,
        description="(A) bias current at half the plateau efficiency; must be below the critical current",
    )
    detection_width_current_a: float = Field(
        default=3.0e-8,
        description="(A) width of the error-function turn-on; smaller is a sharper knee",
    )
    dark_count_rate_hz: float = Field(
        default=100.0,
        description="(Hz) dark counts at the critical current",
    )
    dark_count_doubling_current_a: float = Field(
        default=2.0e-8,
        description="(A) bias increase that doubles the dark count rate",
    )
    pulse_amplitude_mV: float = Field(
        default=200.0,
        description="(mV) height of an output pulse at the counter input",
    )
    pulse_amplitude_spread_mV: float = Field(
        default=20.0,
        description="(mV) spread of pulse heights, which sets how sharply counts fall off with threshold",
    )


class SnspdModel:
    """Live state of one simulated detector: bias in, voltages out.

    A single instance is shared by every simulated instrument on the bench —
    the voltage source writes ``bias_voltage``/``output_enabled``, the
    voltmeter reads :meth:`device_voltage`, the counter counts its pulses and
    the attenuator dims its light. That shared object *is* the wiring between
    them.
    """

    def __init__(self, params: SnspdParams | None = None):
        self.params = params or SnspdParams()
        self.bias_voltage = 0.0
        self.output_enabled = False
        # Fraction of the incident light that reaches the detector, set by
        # whatever attenuator sits in the path. Dark counts do not depend on it.
        self.optical_transmission = 1.0
        self._normal = False
        self._rng = random.Random(self.params.seed)

    # -- what the source does -------------------------------------------------

    def set_bias_voltage(self, voltage: float) -> None:
        self.bias_voltage = float(voltage)

    def set_output_enabled(self, enabled: bool) -> None:
        """An open output sources no current, so the detector sees 0 V."""
        self.output_enabled = bool(enabled)

    # -- what an attenuator does ----------------------------------------------

    def set_optical_transmission(self, fraction: float) -> None:
        """Scale the light reaching the detector: 1 is unattenuated, 0 is dark."""
        self.optical_transmission = min(1.0, max(0.0, float(fraction)))

    # -- what the meter sees --------------------------------------------------

    @property
    def is_normal(self) -> bool:
        """Whether the detector is currently latched into its normal state."""
        return self._normal

    def solve(self) -> tuple[float, float]:
        """Settle the branch for the present bias and return ``(current, voltage)``.

        Calling this advances the hysteresis state, so it is the single place
        the branch may flip. Both readings derive from it, meaning a voltmeter
        read and a current query can never disagree about which branch the
        device is on.
        """
        p = self.params
        applied = self.bias_voltage if self.output_enabled else 0.0

        if not self._normal:
            # Superconducting: the detector is a short, so the bias resistor
            # alone sets the current.
            if abs(applied / p.bias_resistance_ohm) >= p.critical_current_a:
                self._normal = True

        if self._normal:
            current = applied / (p.bias_resistance_ohm + p.normal_resistance_ohm)
            if abs(current) < p.retrapping_current_a:
                self._normal = False
            else:
                return current, current * p.normal_resistance_ohm

        return applied / p.bias_resistance_ohm, 0.0

    def device_voltage(self) -> float:
        """Voltage across the detector, as a voltmeter wired to it would read."""
        _, voltage = self.solve()
        return voltage + self._noise()

    def bias_current(self) -> float:
        """Current through the detector. No instrument reports it — provided
        so tests can state the expected physics directly."""
        current, _ = self.solve()
        return current

    def floating_voltage(self) -> float:
        """What a voltmeter channel wired to nothing reads: noise about zero."""
        return self._noise()

    def _noise(self) -> float:
        if self.params.noise_volts <= 0.0:
            return 0.0
        return self._rng.gauss(0.0, self.params.noise_volts)

    # -- what the counter sees ------------------------------------------------

    def detection_efficiency(self, current: float | None = None) -> float:
        """Fraction of incident photons detected at ``current`` (default: now).

        The error-function turn-on, evaluated by default at the current the
        circuit is actually pushing — so it comes out of the same :meth:`solve`
        as the voltmeter reading and cannot disagree with it.
        """
        amps = abs(self.bias_current() if current is None else current)
        p = self.params
        if p.detection_width_current_a <= 0.0:
            return p.max_detection_efficiency if amps >= p.detection_midpoint_current_a else 0.0
        argument = (amps - p.detection_midpoint_current_a) / (
            math.sqrt(2.0) * p.detection_width_current_a
        )
        return p.max_detection_efficiency * 0.5 * (1.0 + math.erf(argument))

    def dark_count_rate(self, current: float | None = None) -> float:
        """Dark counts per second at ``current`` (default: now).

        Doubling every ``dark_count_doubling_current_a``, normalised so the
        configured rate is the rate *at the critical current*. Stating it there
        rather than at zero bias is what makes the number meaningful: it is the
        end of the curve where dark counts are measured and where they decide
        how far the bias can usefully go.
        """
        p = self.params
        amps = abs(self.bias_current() if current is None else current)
        if p.dark_count_doubling_current_a <= 0.0:
            return p.dark_count_rate_hz
        exponent = (amps - p.critical_current_a) / p.dark_count_doubling_current_a
        return p.dark_count_rate_hz * 2.0**exponent

    def discriminator_fraction(self, threshold_mV: float) -> float:
        """Fraction of pulses that clear a discriminator at ``threshold_mV``.

        One minus the error function of the threshold against the pulse-height
        distribution: everything passes well below the pulse amplitude, nothing
        passes well above it, and the transition is where a threshold sweep
        finds the pulse height.
        """
        p = self.params
        if p.pulse_amplitude_spread_mV <= 0.0:
            return 1.0 if abs(threshold_mV) <= p.pulse_amplitude_mV else 0.0
        argument = (abs(threshold_mV) - p.pulse_amplitude_mV) / (
            math.sqrt(2.0) * p.pulse_amplitude_spread_mV
        )
        return 0.5 * (1.0 - math.erf(argument))

    def count_rate(self, threshold_mV: float = 0.0) -> float:
        """Counts per second a counter on this detector would report.

        Zero once the detector has latched: a device sitting in its normal
        state produces no pulses, which is why a measured PCR curve stops at
        the switching current instead of continuing to climb.
        """
        current, _ = self.solve()  # settles the branch; everything below reads it
        if self._normal or not self.output_enabled:
            return 0.0
        photons = (
            self.params.incident_photon_rate_hz
            * self.optical_transmission
            * self.detection_efficiency(current)
        )
        dark = self.dark_count_rate(current)
        return (photons + dark) * self.discriminator_fraction(threshold_mV)

    def count_events(self, gate_time: float, threshold_mV: float = 0.0) -> int:
        """Events counted in a gate — a Poisson draw about :meth:`count_rate`.

        Drawn rather than rounded because photon arrivals are Poissonian, and a
        PCR curve that came back perfectly smooth would let an analysis that
        ignores counting statistics pass a test it should fail. The draw uses
        the model's seeded generator, so a simulated run is still reproducible.
        """
        if gate_time <= 0.0:
            return 0
        return self._poisson(self.count_rate(threshold_mV) * gate_time)

    def _poisson(self, mean: float) -> int:
        """A Poisson draw, by Knuth's method below the Gaussian crossover."""
        if mean <= 0.0:
            return 0
        if mean > _POISSON_GAUSSIAN_THRESHOLD:
            return max(0, round(self._rng.gauss(mean, math.sqrt(mean))))
        limit = math.exp(-mean)
        count = 0
        product = self._rng.random()
        while product > limit:
            count += 1
            product *= self._rng.random()
        return count
