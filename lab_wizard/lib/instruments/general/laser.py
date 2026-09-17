"""
laser.py

Abstract base class for laser sources.
"""

from __future__ import annotations

from abc import abstractmethod

from lab_wizard.lib.instruments.general.behavior import TERMINAL, InstrumentBehavior
from lab_wizard.lib.instruments.general.state_effects import Arg


class Laser(InstrumentBehavior, specificity=TERMINAL):
    """Abstract base class for a single-channel laser source.

    The third behavior written to the same shape as ``VSource`` and
    ``Attenuator``, and deliberately just as small — a measurement binds to *a*
    laser, not to an AQ2212 module:

    * whether light is emitted — :meth:`turn_on` / :meth:`turn_off` / :meth:`is_output_on`
    * how much                 — :meth:`set_power_dbm` / :meth:`get_power_dbm`
    * at what colour           — :meth:`get_wavelength_nm`

    The getters are not padding, for the reason they are not on ``Attenuator``:
    lasers quantize and clamp their setpoints, and a curve against power means
    what the hardware settled on, not what was asked for.

    **Tuning the wavelength is not in this contract.** Every laser can report
    the wavelength it emits; only a tunable one can be told to change it, and
    nothing choreographs a wavelength sweep yet. Putting ``set_wavelength_nm``
    here would make every fixed-wavelength laser implement a method it must
    refuse. :class:`~lab_wizard.lib.instruments.yokogawaAQ2212.modules.laser.YokoLaser`
    offers it as a driver method, so nothing is lost locally; it moves here when
    a procedure needs to sweep it, alongside whatever says a laser is tunable.

    **Safe state is declared here**: output off. Like the attenuator's, it is
    concrete and built from the abstract primitives, so through a server proxy
    it decomposes into a recorded ``turn_off`` rather than one opaque RPC the
    permission gate cannot see into.

    **Units** are dBm for power and nanometres for wavelength.
    """

    # Safety-state declarations for the permission gate, inherited by every
    # Laser subclass and merged across the MRO — so a rule can say "deny opening
    # a shutter while the laser output is on".
    _state_methods_ = {
        "turn_on": ("output", "on"),
        "turn_off": ("output", "off"),
        "set_power_dbm": ("power_dbm", Arg(0)),
    }
    _query_methods_ = frozenset({"get_power_dbm", "get_wavelength_nm", "is_output_on"})

    @abstractmethod
    def turn_on(self) -> bool:
        """Start emitting. Returns True on success."""

    @abstractmethod
    def turn_off(self) -> bool:
        """Stop emitting. Returns True on success."""

    @abstractmethod
    def is_output_on(self) -> bool:
        """Whether the laser is emitting, as the hardware holds it."""

    @abstractmethod
    def set_power_dbm(self, power_dbm: float) -> bool:
        """Set the output power in dBm. Returns True on success.

        Instruments quantize and clamp; :meth:`get_power_dbm` reports what was
        actually applied.
        """

    @abstractmethod
    def get_power_dbm(self) -> float:
        """Output power in dBm, as the hardware holds it."""

    @abstractmethod
    def get_wavelength_nm(self) -> float:
        """The wavelength being emitted, in nanometres."""

    def enter_safe_state(self) -> bool:
        """Stop emitting.

        A laser's whole hazard is light leaving it, so unlike the attenuator's
        two-step safe state there is exactly one thing to do.
        """
        return bool(self.turn_off())


class StandInLaser(Laser):
    """Stand-in used when no laser is available — prints instead of emitting."""

    ignore_in_cli = True

    def __init__(self):
        self.output_on = False
        self.power_dbm = 0.0
        self.wavelength_nm = 1550.0
        print("Stand-in laser initialized.")

    def turn_on(self) -> bool:
        print("Stand-in: laser on")
        self.output_on = True
        return True

    def turn_off(self) -> bool:
        print("Stand-in: laser off")
        self.output_on = False
        return True

    def is_output_on(self) -> bool:
        return self.output_on

    def set_power_dbm(self, power_dbm: float) -> bool:
        print(f"Stand-in: setting laser power to {power_dbm} dBm")
        self.power_dbm = power_dbm
        return True

    def get_power_dbm(self) -> float:
        return self.power_dbm

    def get_wavelength_nm(self) -> float:
        return self.wavelength_nm
