from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.attenuator import Attenuator
from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.andoAQ8201A.children import AndoAQ8201AModuleParams
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201ASlotDep


class Attenuator31Params(SlotLike, AndoAQ8201AModuleParams):
    type: Literal["ando_attenuator31"] = "ando_attenuator31"
    attribute_name: str = ""
    offline: bool = False
    min_attenuation: float = 0.0
    max_attenuation: float = 60.0
    wavelength_nm: float = 1550.0

    @classmethod
    def resource_class(cls):
        return Attenuator31


class Attenuator31(Child[AndoAQ8201ASlotDep, Attenuator31Params], Attenuator):
    """Ando AQ8201-31 Variable Optical Attenuator Module, as an :class:`Attenuator`.

    The AQ8201-31 has no shutter read-back, which is why reading the shutter is
    not part of the ABC.
    """

    def __init__(self, dep: AndoAQ8201ASlotDep, params: Attenuator31Params):
        self._dep = dep
        self.params = params
        self.slot = dep.slot
        self.attribute_name = params.attribute_name
        # Offline, the dep answers every query with "", so the getter reports
        # the last commanded value instead of failing to parse nothing.
        self._commanded_attenuation_db = 0.0

    @property
    def offline(self) -> bool:
        return bool(self.params.offline or getattr(self._dep, "offline", False))

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

    # ---- Attenuator contract ----------------------------------------------

    def set_attenuation(self, attenuation_db: float) -> bool:
        self._dep.write(f"AAV {attenuation_db}")
        self._commanded_attenuation_db = float(attenuation_db)
        return True

    def get_attenuation(self) -> float:
        # The module reports attenuation only alongside wavelength, in one
        # status reply; there is no attenuation-only query.
        if self.offline:
            return self._commanded_attenuation_db
        return self.get_status()[1]

    def open_shutter(self) -> bool:
        self._dep.write("ASHTR 0")
        return True

    def close_shutter(self) -> bool:
        self._dep.write("ASHTR 1")
        return True

    def get_max_attenuation(self) -> float:
        return self.params.max_attenuation
