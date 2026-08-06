"""FAKE970 — a SIM970 voltmeter reading a simulated detector.

The driver is :class:`Sim970`, unchanged, so the channels handed to a
measurement are real :class:`Sim970Channel` objects issuing real ``VOLT? n``
queries. ``device_channel`` says which of the four inputs is wired across the
detector; the others float and read noise about zero, as unconnected inputs do.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel
from lab_wizard.lib.instruments.fake_rack.virtual_rack import (
    VirtualSlotModule,
    VirtualVoltmeter,
)
from lab_wizard.lib.instruments.general.parent_child import (
    ChannelsLike,
    SlotLike,
)
from lab_wizard.lib.instruments.fake_rack.children import Fake900ModuleParams
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970


class Fake970ChannelParams(BaseModel):
    """Per-channel configuration for one simulated voltmeter channel.

    ``settling_time`` defaults to zero for the same reason as on the fake
    source: nothing physical is settling, and the real module's 0.1 s default
    would dominate the runtime of every simulated sweep.
    """

    attribute_name: str = ""
    settling_time: float = Field(
        default=0.0,
        description="(seconds)",
    )
    max_retries: int = 3


class Fake970Params(ChannelsLike, SlotLike, Fake900ModuleParams):
    """Parameters for the simulated voltmeter module."""

    type: Literal["fake970"] = "fake970"
    attribute_name: str = ""
    offline: bool | None = False
    num_channels: ClassVar[int] = 4
    device_channel: int = Field(
        default=0,
        description="Which channel (0-based) is wired across the simulated detector",
    )
    channels: dict[int, Fake970ChannelParams] = Field(default_factory=dict)

    @classmethod
    def resource_class(cls):
        return Fake970

    def virtual_module(self, model: SnspdModel) -> VirtualSlotModule:
        """The emulated hardware this params entry stands for."""
        return VirtualVoltmeter(
            model,
            device_channel=self.device_channel,
            num_channels=type(self).num_channels,
        )


class Fake970(Sim970):
    """SIM970 driver pointed at a simulated slot.

    Channel construction, retry logic, and reply parsing are inherited from
    :class:`Sim970`; the channels it builds are ordinary
    :class:`~lab_wizard.lib.instruments.sim900.modules.sim970.Sim970Channel`
    objects and satisfy ``VSense`` exactly as the real ones do.
    """
