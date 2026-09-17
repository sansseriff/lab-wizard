from __future__ import annotations

from typing import Literal

from pydantic import Field

from lab_wizard.lib.instruments.general.laser import Laser
from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.children import YokogawaAQ2212ModuleParams
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep

_c = 299792458.0  # speed of light m/s


class YokoLaserParams(SlotLike, YokogawaAQ2212ModuleParams):
    type: Literal["yoko_laser"] = "yoko_laser"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = Field(
        default=1550.0,
        description="(nm) wavelength this laser is set to at the start of every run",
    )

    @classmethod
    def resource_class(cls):
        return YokoLaser


class YokoLaser(Child[YokoAQ2212SlotDep, YokoLaserParams], Laser):
    """AQ2212 tunable laser module, as a :class:`Laser`.

    Named for its vendor because ``Laser`` is the behavior it satisfies. The
    params ``type`` is still ``yoko_laser``, and instrument hashes derive from
    that literal rather than the class name, so configs written before the
    rename load unchanged.

    The module speaks in frequency — ``SOUR<n>:FREQ`` in hertz — while every
    measurement and every optics bench speaks in nanometres, so the conversion
    lives here rather than in anything that uses it.
    """

    _query_methods_ = frozenset({"get_frequency_wavelength"})

    def __init__(self, dep: YokoAQ2212SlotDep, params: YokoLaserParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot
        self.attribute_name = params.attribute_name
        # Offline, the dep answers every query with "", so the getters report
        # what was last commanded instead of failing to parse nothing.
        self._commanded_power_dbm = 0.0
        self._commanded_output_on = False
        self._commanded_wavelength_nm = params.wavelength_nm

    @property
    def offline(self) -> bool:
        return bool(self.params.offline or getattr(self._dep, "offline", False))

    # ---- Laser contract ----------------------------------------------------

    def turn_on(self) -> bool:
        self._dep.write(f"SOUR{self.slot}:POW:STAT ON")
        self._commanded_output_on = True
        return True

    def turn_off(self) -> bool:
        self._dep.write(f"SOUR{self.slot}:POW:STAT OFF")
        self._commanded_output_on = False
        return True

    def is_output_on(self) -> bool:
        if self.offline:
            return self._commanded_output_on
        return bool(int(self._dep.query(f"SOUR{self.slot}:POW:STAT?")))

    def set_power_dbm(self, power_dbm: float) -> bool:
        self._dep.write(f"SOUR{self.slot}:POW:AMPL {power_dbm}")
        self._commanded_power_dbm = float(power_dbm)
        return True

    def get_power_dbm(self) -> float:
        if self.offline:
            return self._commanded_power_dbm
        return float(self._dep.query(f"SOUR{self.slot}:POW:AMPL?"))

    def get_wavelength_nm(self) -> float:
        if self.offline:
            return self._commanded_wavelength_nm
        return self.get_frequency_wavelength()[1]

    def apply_baseline(self) -> bool:
        """Tune to the configured wavelength.

        Which colour this laser emits is a fact about the bench, so it lives in
        the params and is re-applied at the start of every run — a wavelength
        left behind by someone else's experiment would silently change what a
        count rate means.
        """
        return self.set_wavelength_nm(self.params.wavelength_nm)

    # ---- Driver extras -----------------------------------------------------

    def set_wavelength_nm(self, wavelength_nm: float) -> bool:
        """Tune the laser. Not part of the ``Laser`` contract — see that ABC."""
        freq_hz = round(_c / (wavelength_nm * 1e-9), 1)
        self._dep.write(f"SOUR{self.slot}:FREQ {freq_hz}")
        self._commanded_wavelength_nm = float(wavelength_nm)
        return True

    def get_frequency_wavelength(self) -> tuple[float, float]:
        """Returns (freq_hz, wavelength_nm)."""
        freq = float(self._dep.query(f"SOUR{self.slot}:FREQ?"))
        wav_nm = round((_c / freq) * 1e9, 3)
        return freq, wav_nm
