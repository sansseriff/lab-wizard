from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep


class PowerMeterParams(SlotLike, ChildParams["PowerMeter"]):
    type: Literal["yoko_power_meter"] = "yoko_power_meter"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = 1550.0

    @property
    def inst(self):
        return PowerMeter


class PowerMeter(Child[YokoAQ2212SlotDep, PowerMeterParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: PowerMeterParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212.YokogawaAQ2212"

    def get_power_fetch_dbm(self) -> float:
        """Fetch displayed power value (dBm); includes power offset."""
        return float(self._dep.query(f"FETC{self.slot}:POW?"))

    def get_power_read_w(self) -> float:
        """Single power read measurement (W)."""
        return float(self._dep.query(f"READ{self.slot}:POW?"))

    def set_averaging_time(self, avgtime: float) -> None:
        self._dep.write(f"SENS{self.slot}:POW:ATIM {avgtime}")

    def get_averaging_time(self) -> float:
        return float(self._dep.query(f"SENS{self.slot}:POW:ATIM?"))

    def set_wavelength_nm(self, wav_nm: float) -> None:
        self._dep.write(f"SENS{self.slot}:POW:WAV +{wav_nm}E-009")

    def get_wavelength_nm(self) -> str:
        return self._dep.query(f"SENS{self.slot}:POW:WAV?")
