"""
attenuator.py

Abstract base class for variable optical attenuators.
"""

from __future__ import annotations

from abc import abstractmethod

from lab_wizard.lib.instruments.general.behavior import TERMINAL, InstrumentBehavior
from lab_wizard.lib.instruments.general.state_effects import Arg


class Attenuator(InstrumentBehavior, specificity=TERMINAL):
    """Abstract base class for a single-channel variable optical attenuator.

    An attenuator sets how much light gets through. That is the whole contract,
    deliberately — an MCR sweep binds to *an* attenuator, not to a Yokogawa
    module, so what lives here is only what such a measurement cannot work
    without:

    * how much to attenuate     — :meth:`set_attenuation` / :meth:`get_attenuation`
    * whether light passes      — :meth:`open_shutter` / :meth:`close_shutter`
    * where the range ends      — :meth:`get_max_attenuation`

    The getter is not padding. Attenuators quantize and clamp, so the value
    that was asked for is not necessarily the value applied, and a count-rate
    curve against attenuation is only interpretable against the latter.

    Wavelength is **not** here. It is a fact about the bench — which laser is
    plugged in — so it belongs in each driver's params, not in a contract a
    procedure sweeps. See ``plans/procedure_plan.md`` 2.1.

    **Safe state is declared here**, not in whatever step happens to guard a
    run: shutter closed, attenuation at maximum. :meth:`enter_safe_state` is
    concrete and built from the abstract primitives, so through a server proxy
    it decomposes into ``close_shutter`` and ``set_attenuation`` calls that the
    permission gate records individually — forwarding it as one opaque RPC
    would hide the shutter closing from the gate.

    **Units** are decibels throughout.

    **Single channel, like Counter.** A multi-channel attenuator is modelled as
    one Attenuator per channel held by a ``ChannelProvider``, so a measurement
    is handed the one path it controls.
    """

    # Safety-state declarations for the permission gate, inherited by every
    # Attenuator subclass and merged across the MRO — so a rule can say "deny
    # turning the laser on while the shutter is open".
    _state_methods_ = {
        "set_attenuation": ("attenuation_db", Arg(0)),
        "open_shutter": ("shutter", "open"),
        "close_shutter": ("shutter", "closed"),
    }
    _query_methods_ = frozenset({"get_attenuation", "get_max_attenuation"})

    @abstractmethod
    def set_attenuation(self, attenuation_db: float) -> bool:
        """Set the attenuation in dB. Returns True on success.

        Instruments quantize and clamp; :meth:`get_attenuation` reports what
        was actually applied.
        """

    @abstractmethod
    def get_attenuation(self) -> float:
        """Attenuation in dB, as the hardware holds it."""

    @abstractmethod
    def open_shutter(self) -> bool:
        """Let light through. Returns True on success."""

    @abstractmethod
    def close_shutter(self) -> bool:
        """Block light entirely. Returns True on success."""

    @abstractmethod
    def get_max_attenuation(self) -> float:
        """The highest attenuation in dB this attenuator can be set to."""

    def enter_safe_state(self) -> bool:
        """Block light: close the shutter, then drive to maximum attenuation.

        Shutter first, because it is the faster of the two and blocks light
        completely; maximum attenuation follows so the path stays dark if the
        shutter is later opened without a new setting. Both are attempted even
        if the first reports failure — a half-safe attenuator is still safer
        than one that stopped trying.
        """
        closed = self.close_shutter()
        maxed = self.set_attenuation(self.get_max_attenuation())
        return bool(closed and maxed)


class StandInAttenuator(Attenuator):
    """
    Stand-in class for Attenuator.
    This class is used when the actual attenuator instrument is not available.
    It provides default implementations for all methods.
    """

    ignore_in_cli = True

    def __init__(self):
        self.attenuation_db = 0.0
        self.shutter_open = False
        self.max_attenuation_db = 60.0
        print("Stand-in attenuator initialized.")

    def set_attenuation(self, attenuation_db: float) -> bool:
        print(f"Stand-in: Setting attenuation to {attenuation_db} dB")
        self.attenuation_db = attenuation_db
        return True

    def get_attenuation(self) -> float:
        return self.attenuation_db

    def open_shutter(self) -> bool:
        print("Stand-in: Opening shutter")
        self.shutter_open = True
        return True

    def close_shutter(self) -> bool:
        print("Stand-in: Closing shutter")
        self.shutter_open = False
        return True

    def get_max_attenuation(self) -> float:
        return self.max_attenuation_db
