from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep


class AttenuatorParams(SlotLike, ChildParams["Attenuator"]):
    type: Literal["yoko_attenuator"] = "yoko_attenuator"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = 1550.0

    @property
    def inst(self):
        return Attenuator


class Attenuator(Child[YokoAQ2212SlotDep, AttenuatorParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: AttenuatorParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212.YokogawaAQ2212"

    def get_attenuation(self) -> float:
        return float(self._dep.query(f"INP{self.slot}:ATT?"))

    def set_attenuation(self, atten_db: float) -> None:
        self._dep.write(f"INP{self.slot}:ATT {atten_db}")

    def get_wavelength_nm(self) -> float:
        return float(self._dep.query(f"INP{self.slot}:WAV?")) * 1e9

    def set_wavelength_nm(self, wav_nm: float) -> None:
        self._dep.write(f"INP{self.slot}:WAV +{wav_nm}E-009")

    def set_output(self, enabled: bool) -> None:
        self._dep.write(f"OUTP{self.slot}:STAT {int(enabled)}")

    def get_output_status(self) -> bool:
        return bool(int(self._dep.query(f"OUTP{self.slot}:STAT?")))
