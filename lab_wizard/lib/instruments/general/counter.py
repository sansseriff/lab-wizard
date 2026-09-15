"""
counter.py

Abstract base class for event counters (photon counters, universal counters,
discriminator/scaler pairs).
"""

from __future__ import annotations

import math
from abc import abstractmethod

from lab_wizard.lib.instruments.general.behavior import TERMINAL, InstrumentBehavior


class Counter(InstrumentBehavior, specificity=TERMINAL):
    """Abstract base class for a single-channel event counter.

    A counter turns a pulse train into a number: how many edges crossed a
    discriminator threshold during a gate of known length. That is the whole
    contract, deliberately — a PCR sweep binds to *a* counter, not to a
    Keysight, so what lives here is only what such a measurement cannot work
    without:

    * how long to count for      — :meth:`set_gate_time` / :meth:`get_gate_time`
    * what counts as a pulse     — :meth:`set_threshold` / :meth:`get_threshold`
    * the count itself           — :meth:`count`

    The two getters are not padding. A counter quantizes both settings to its
    own grid (the 53220A rounds thresholds to 2.5 mV), so the value that was
    asked for is not the value that was used, and a photon-count-rate curve is
    only interpretable against the latter.

    Everything richer — external gating, continuous totalizing, input coupling
    and impedance, buffered multi-reading acquisition — is real and useful but
    not universal, so it belongs on the concrete driver rather than here. See
    :class:`~lab_wizard.lib.instruments.keysight53220A.Keysight53220AChannel`.

    **Single channel, like VSense.** A two-input counter is modelled as two
    Counter objects held by a ``ChannelProvider``, not as one object taking a
    channel argument on every call. A measurement is then handed the one input
    it counts on and cannot address a neighbour's detector by passing the wrong
    integer.

    **Units** are seconds for gate times and millivolts for thresholds.
    Millivolts because SNSPD discriminator levels are tens of mV and writing
    them in volts turns every experiment note into a string of zeros; the
    driver converts to the instrument's units at the SCPI boundary.

    Lifetime follows RAII: the connection is opened by the constructor and the
    underlying transport (e.g. ``LocalVisaDep``) releases its handle through
    its own ``__del__``/``atexit``. Subclasses do not implement ``disconnect``.
    """

    # Only the getters. ``count``, ``count_rate`` and ``measure`` are *writes*:
    # on a real counter they configure and arm the instrument for this input.
    _query_methods_ = frozenset({"get_gate_time", "get_threshold"})

    @abstractmethod
    def count(self, gate_time: float | None = None) -> int:
        """Count events for one gate and return the number of events.

        Args:
            gate_time: Gate length in seconds. ``None`` uses the counter's
                current gate time, so a caller that set it once need not
                repeat it on every point.

        Returns:
            int: Events counted during the gate.
        """

    @abstractmethod
    def set_gate_time(self, gate_time: float) -> bool:
        """Set the gate length in seconds. Returns True on success."""

    @abstractmethod
    def get_gate_time(self) -> float:
        """Gate length in seconds that the next count will actually use."""

    @abstractmethod
    def set_threshold(self, threshold_mV: float) -> bool:
        """Set the discriminator threshold in millivolts.

        A pulse is counted when it crosses this level on the counter's active
        edge. Instruments quantize and clamp; :meth:`get_threshold` reports
        what was actually applied.
        """

    @abstractmethod
    def get_threshold(self) -> float:
        """Discriminator threshold in millivolts, as the hardware holds it."""

    # ---- Convenience wrappers ----

    def count_rate(self, gate_time: float | None = None) -> float:
        """Counts per second over one gate — the quantity a PCR curve plots."""
        gate = self.get_gate_time() if gate_time is None else gate_time
        counts = self.count(gate)
        return counts / gate if gate > 0 else math.nan

    def measure(self) -> float:
        """Return a scalar reading (count rate in Hz).

        The alias measurement code uses when it treats a counter like any
        other single-number instrument, matching ``VSense.measure()``.
        """
        return self.count_rate()


class StandInCounter(Counter):
    """
    Stand-in class for Counter.
    This class is used when the actual counter instrument is not available.
    It provides default implementations for all methods.
    """

    ignore_in_cli = True

    def __init__(self):
        self.gate_time = 1.0
        self.threshold_mV = 0.0
        print("Stand-in counter instrument initialized.")

    def count(self, gate_time: float | None = None) -> int:
        gate = self.gate_time if gate_time is None else gate_time
        print(f"Stand-in: Counting for {gate}s")
        return 0  # Default count value

    def set_gate_time(self, gate_time: float) -> bool:
        print(f"Stand-in: Setting gate time to {gate_time}s")
        self.gate_time = gate_time
        return True

    def get_gate_time(self) -> float:
        return self.gate_time

    def set_threshold(self, threshold_mV: float) -> bool:
        print(f"Stand-in: Setting threshold to {threshold_mV} mV")
        self.threshold_mV = threshold_mV
        return True

    def get_threshold(self) -> float:
        return self.threshold_mV
