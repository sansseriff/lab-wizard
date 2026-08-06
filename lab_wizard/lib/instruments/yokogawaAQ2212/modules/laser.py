from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.children import YokogawaAQ2212ModuleParams
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep

_c = 299792458.0  # speed of light m/s


class LaserParams(SlotLike, YokogawaAQ2212ModuleParams):
    type: Literal["yoko_laser"] = "yoko_laser"
    attribute_name: str = ""
    offline: bool = False

    @classmethod
    def resource_class(cls):
        return Laser


class Laser(Child[YokoAQ2212SlotDep, LaserParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: LaserParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    def get_status(self) -> int:
        return int(self._dep.query(f"SOUR{self.slot}:POW:STAT?"))

    def set_output(self, enabled: bool) -> None:
        val = "ON" if enabled else "OFF"
        self._dep.write(f"SOUR{self.slot}:POW:STAT {val}")

    def get_frequency_wavelength(self) -> tuple[float, float]:
        """Returns (freq_hz, wavelength_nm)."""
        freq = float(self._dep.query(f"SOUR{self.slot}:FREQ?"))
        wav_nm = round((_c / freq) * 1e9, 3)
        return freq, wav_nm

    def set_wavelength_nm(self, wav_nm: float) -> None:
        freq_hz = round(_c / (wav_nm * 1e-9), 1)
        self._dep.write(f"SOUR{self.slot}:FREQ {freq_hz}")

    def get_power_dbm(self) -> float:
        return float(self._dep.query(f"SOUR{self.slot}:POW:AMPL?"))

    def set_power_dbm(self, dbm: float) -> None:
        self._dep.write(f"SOUR{self.slot}:POW:AMPL {dbm}")
