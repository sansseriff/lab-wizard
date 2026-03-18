from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201ASlotDep


class Attenuator31Params(SlotLike, ChildParams["Attenuator31"]):
    type: Literal["ando_attenuator31"] = "ando_attenuator31"
    attribute_name: str = ""
    offline: bool = False
    min_attenuation: float = 0.0
    max_attenuation: float = 60.0
    wavelength_nm: float = 1550.0

    @property
    def inst(self):
        return Attenuator31


class Attenuator31(Child[AndoAQ8201ASlotDep, Attenuator31Params]):
    """Ando AQ8201-31 Variable Optical Attenuator Module."""

    def __init__(self, dep: AndoAQ8201ASlotDep, params: Attenuator31Params):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A.AndoAQ8201A"

    def get_status(self) -> tuple[int, float]:
        """Returns (wavelength_nm, attenuation_db)."""
        response = self._dep.query("AD?")
        parts = response.split()
        wavelength = int(parts[0][6:10])
        attenuation = float(parts[1])
        return wavelength, attenuation

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        wav = int(round(wavelength_nm))
        self._dep.write(f"AW {wav}")

    def set_attenuation_db(self, attenuation_db: float) -> None:
        self._dep.write(f"AAV {attenuation_db}")

    def open_shutter(self) -> None:
        self._dep.write("ASHTR 0")

    def close_shutter(self) -> None:
        self._dep.write("ASHTR 1")
