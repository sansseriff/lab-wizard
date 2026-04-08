from typing import Literal, Any
from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, SlotLike
from lab_wizard.lib.instruments.general.vsource import VSource


class Dac4DChannelParams(BaseModel):
    """Per-channel configuration for a single Dac4D output channel."""
    attribute_name: str = Field(default="")


class Dac4DChannel(VSource):
    """Single output channel for Dac4D."""

    def __init__(self, module: Any, channel_index: int, params: Dac4DChannelParams):
        self.module = module
        self.channel_index = channel_index
        self.attribute_name = params.attribute_name

    def set_voltage(self, voltage: float) -> bool:  # type: ignore[override]
        try:
            self.module.set_voltage(self.channel_index, voltage)
            return True
        except Exception as e:
            print(f"Error setting voltage on channel {self.channel_index}: {e}")
            return False

    def turn_on(self) -> bool:  # type: ignore[override]
        return self.set_voltage(0.0)

    def turn_off(self) -> bool:  # type: ignore[override]
        return self.set_voltage(0.0)

    def disconnect(self) -> bool:  # type: ignore[override]
        return True


"""
Dac4DParams stores the 'path' to any particular channels in use.

If a channel is to be used in an experiment, Dac4DParams will be given a key/value pair
in the channels list, and the Dac4DChannelParams class will be given an attribute_name string.
"""


class Dac4DParams(SlotLike, ChildParams["Dac4D"]):
    type: Literal["dac4D"] = "dac4D"
    name: str = "Dac4D"
    channels: list[Dac4DChannelParams] = Field(default_factory=lambda: [Dac4DChannelParams() for _ in range(4)])

    @property
    def inst(self):  # type: ignore[override]
        return Dac4D


class Dac4D(Child[Any, Dac4DParams], ChannelProvider[Dac4DChannel]):
    def __init__(self, module: Any, params: Dac4DParams):
        self.module = module
        self.params = params
        self.channels: list[Dac4DChannel] = [
            Dac4DChannel(module, i, ch_params)
            for i, ch_params in enumerate(params.channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @property
    def dep(self) -> Any:
        return self.module

    def __str__(self) -> str:
        return f"Dac4D (Slot {self.params.slot}): {len(self.channels)} channels"
