"""A one-detector SNSPD circuit model — the physics the fake rack reports.

The fake rack (:mod:`lab_wizard.lib.instruments.fake_rack.virtual_rack`) speaks
the real SIM900 wire protocol; this module is what gives it something true to
say back. Nothing here knows about serial ports, GPIB, or params trees: it is a
pure circuit so it can be reasoned about and tested on its own.

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

This throwaway model fixes that resistor at 100 kΩ, matching the IV
measurement's existing default, so it does not add simulation-only settings to
experiment YAML. The default detector therefore switches at 0.03 V applied.
"""

from __future__ import annotations

import random

from pydantic import BaseModel, Field


BIAS_RESISTANCE_OHM = 100_000.0


class SnspdModelParams(BaseModel):
    """Device and circuit constants for one simulated detector."""

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


class SnspdModel:
    """Live state of one simulated detector: bias in, voltages out.

    A single instance is shared by every virtual module wired to it — the
    voltage source writes ``bias_voltage``/``output_enabled``, the voltmeter
    reads :meth:`device_voltage`. That shared object *is* the wiring between
    them, which is why the fake mainframe owns it rather than the modules.
    """

    def __init__(self, params: SnspdModelParams | None = None):
        self.params = params or SnspdModelParams()
        self.bias_voltage = 0.0
        self.output_enabled = False
        self._normal = False
        self._rng = random.Random(self.params.seed)

    # -- what the source does -------------------------------------------------

    def set_bias_voltage(self, voltage: float) -> None:
        self.bias_voltage = float(voltage)

    def set_output_enabled(self, enabled: bool) -> None:
        """An open output sources no current, so the detector sees 0 V."""
        self.output_enabled = bool(enabled)

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
            if abs(applied / BIAS_RESISTANCE_OHM) >= p.critical_current_a:
                self._normal = True

        if self._normal:
            current = applied / (BIAS_RESISTANCE_OHM + p.normal_resistance_ohm)
            if abs(current) < p.retrapping_current_a:
                self._normal = False
            else:
                return current, current * p.normal_resistance_ohm

        return applied / BIAS_RESISTANCE_OHM, 0.0

    def device_voltage(self) -> float:
        """Voltage across the detector, as a voltmeter wired to it would read."""
        _, voltage = self.solve()
        return voltage + self._noise()

    def bias_current(self) -> float:
        """Current through the detector. Not something the rack reports —
        provided so tests can state the expected physics directly."""
        current, _ = self.solve()
        return current

    def floating_voltage(self) -> float:
        """What a voltmeter channel wired to nothing reads: noise about zero."""
        return self._noise()

    def _noise(self) -> float:
        if self.params.noise_volts <= 0.0:
            return 0.0
        return self._rng.gauss(0.0, self.params.noise_volts)
