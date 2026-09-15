from __future__ import annotations

from typing import Literal

from pydantic import Field

from lab_wizard.lib.instruments.general.attenuator import Attenuator
from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.children import YokogawaAQ2212ModuleParams
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep


class YokoAttenuatorParams(SlotLike, YokogawaAQ2212ModuleParams):
    type: Literal["yoko_attenuator"] = "yoko_attenuator"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = 1550.0
    max_attenuation: float = Field(
        default=60.0,
        description="(dB) highest settable attenuation; the safe-state target",
    )

    @classmethod
    def resource_class(cls):
        return YokoAttenuator


class YokoAttenuator(Child[YokoAQ2212SlotDep, YokoAttenuatorParams], Attenuator):
    """AQ2212 variable optical attenuator module, as an :class:`Attenuator`.

    Named for its vendor because ``Attenuator`` is the behavior it satisfies.
    The params ``type`` is still ``yoko_attenuator``, and instrument hashes are
    derived from that literal rather than the class name, so configs written
    before the rename load unchanged.

    The AQ2212 calls its shutter the module's *output*: ``OUTP<n>:STAT 1``
    passes light and ``0`` blocks it.
    """

    _query_methods_ = frozenset({"is_shutter_open", "get_wavelength_nm"})

    def __init__(self, dep: YokoAQ2212SlotDep, params: YokoAttenuatorParams):
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

    # ---- Attenuator contract ----------------------------------------------

    def set_attenuation(self, attenuation_db: float) -> bool:
        self._dep.write(f"INP{self.slot}:ATT {attenuation_db}")
        self._commanded_attenuation_db = float(attenuation_db)
        return True

    def get_attenuation(self) -> float:
        if self.offline:
            return self._commanded_attenuation_db
        return float(self._dep.query(f"INP{self.slot}:ATT?"))

    def open_shutter(self) -> bool:
        self._dep.write(f"OUTP{self.slot}:STAT 1")
        return True

    def close_shutter(self) -> bool:
        self._dep.write(f"OUTP{self.slot}:STAT 0")
        return True

    def get_max_attenuation(self) -> float:
        return self.params.max_attenuation

    def apply_baseline(self) -> bool:
        """Set the calibration wavelength to the configured one.

        The attenuation calibration depends on wavelength, which is a fact about
        the bench (which laser is plugged in). Until this, ``wavelength_nm`` was
        stored and never sent, so the module kept whatever was last set.
        """
        self.set_wavelength_nm(self.params.wavelength_nm)
        return True

    # ---- AQ2212-specific ---------------------------------------------------

    def is_shutter_open(self) -> bool:
        """Read the shutter back. Not on the ABC — not every attenuator can."""
        return bool(int(self._dep.query(f"OUTP{self.slot}:STAT?")))

    def get_wavelength_nm(self) -> float:
        return float(self._dep.query(f"INP{self.slot}:WAV?")) * 1e9

    def set_wavelength_nm(self, wav_nm: float) -> None:
        self._dep.write(f"INP{self.slot}:WAV +{wav_nm}E-009")
