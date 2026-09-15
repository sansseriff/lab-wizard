"""FAKE_ATTENUATOR — a variable optical attenuator in front of a simulated detector.

A standalone root, like the simulated counter: an attenuator is its own box in
the light path, not a module in the bias rack, so it names the detector it sits
in front of with ``detector_name`` rather than inheriting one from a parent.
Give it the same name as the rack and counter, and attenuating here dims the
count rate the counter reports — which is what makes a simulated MCR curve mean
anything.

The physics is the textbook one: transmission is ``10 ** (-dB / 10)``, a closed
shutter transmits nothing, and dark counts are unaffected by either.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Literal

from pydantic import Field

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams
from lab_wizard.lib.instruments.fake_rack.wiring import shared_detector
from lab_wizard.lib.instruments.general.attenuator import Attenuator
from lab_wizard.lib.instruments.general.parent_child import CanInstantiate, Instrument, USBLike
from lab_wizard.lib.instruments.general.transport import TransportSharing
from lab_wizard.lib.utilities.model_tree import ResourceConfig

logger = logging.getLogger("lab_wizard.lib.instruments.fake_rack.fake_attenuator")

DEFAULT_FAKE_ATTENUATOR_ADDRESS = "sim://fake-attenuator-0"

# The resolution a real programmable attenuator sets to; get_attenuation
# reports the quantized value, as the ABC requires.
_RESOLUTION_DB = 0.001


class FakeAttenuatorParams(USBLike, CanInstantiate["FakeAttenuator"]):
    """Params for the simulated attenuator."""

    type: Literal["fake_attenuator"] = "fake_attenuator"
    port: str = DEFAULT_FAKE_ATTENUATOR_ADDRESS
    key_hint: ClassVar[str] = "Simulated address (e.g. sim://fake-attenuator-0)"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = Field(default=1550.0, description="(nm) calibration wavelength")
    max_attenuation: float = Field(
        default=60.0,
        description="(dB) highest settable attenuation; the safe-state target",
    )
    device: SnspdModelParams = Field(
        default_factory=SnspdModelParams,
        description="Constants for the detector, if this instrument is the one that creates it",
    )
    detector_name: str = Field(
        default="",
        description=(
            "Name of the shared detector this attenuator sits in front of; must "
            "match the rack and counter. Empty dims a private detector nothing reads"
        ),
    )

    @classmethod
    def resource_class(cls) -> type["FakeAttenuator"]:
        return FakeAttenuator

    def create_inst(self) -> "FakeAttenuator":
        return FakeAttenuator.from_params(self)

    def transport_sharing(self) -> TransportSharing:
        return "exclusive"

    def transport_key(self) -> str | None:
        return self.port or None


class FakeAttenuator(Instrument, Attenuator):
    """A single-channel attenuator that sets the shared detector's optical transmission."""

    def __init__(self, model: SnspdModel, params: FakeAttenuatorParams) -> None:
        self.model = model
        self.params = params
        self.attribute_name = params.attribute_name
        self.attenuation_db = 0.0
        self.shutter_open = True
        self.wavelength_nm = params.wavelength_nm

    @classmethod
    def from_params(cls, params: FakeAttenuatorParams) -> "FakeAttenuator":
        if not params.detector_name:
            logger.warning(
                "Simulated attenuator %s has no detector_name, so it dims a private "
                "detector nothing counts. Set detector_name to match the counter.",
                params.port,
            )
        return cls(shared_detector(params.detector_name, params.device), params)

    @classmethod
    def from_config(cls, resources: ResourceConfig, *, key: str) -> "FakeAttenuator":
        raw = resources.instruments[key]
        if not isinstance(raw, FakeAttenuatorParams):
            raise TypeError(f"Expected FakeAttenuatorParams at resources.instruments[{key!r}]")
        return cls.from_params(raw)

    def _apply(self) -> None:
        transmission = 10.0 ** (-self.attenuation_db / 10.0) if self.shutter_open else 0.0
        self.model.set_optical_transmission(transmission)

    # ---- Attenuator contract ----------------------------------------------

    def set_attenuation(self, attenuation_db: float) -> bool:
        clamped = min(self.params.max_attenuation, max(0.0, float(attenuation_db)))
        self.attenuation_db = round(clamped / _RESOLUTION_DB) * _RESOLUTION_DB
        self._apply()
        return True

    def get_attenuation(self) -> float:
        return self.attenuation_db

    def open_shutter(self) -> bool:
        self.shutter_open = True
        self._apply()
        return True

    def close_shutter(self) -> bool:
        self.shutter_open = False
        self._apply()
        return True

    def get_max_attenuation(self) -> float:
        return self.params.max_attenuation

    def apply_baseline(self) -> bool:
        self.wavelength_nm = self.params.wavelength_nm
        return True
