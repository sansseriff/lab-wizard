from typing import Any, ClassVar, Literal
from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, ChannelsLike, SlotLike
from lab_wizard.lib.instruments.general.vsense import VSense


class Adc4DChannelParams(BaseModel):
    """Per-channel configuration for a single Adc4D differential input channel."""
    attribute_name: str = Field(default="")


class Adc4DChannel(VSense):
    """Single differential input channel for Adc4D, implementing VSense.

    Mirrors Dac4DChannel/VSource but for sensing: get_voltage triggers a
    differential read on the underlying dbay ADC4D module (``read_diff``),
    which returns the measured voltage in both gui and direct modes.
    """

    def __init__(self, module: Any, channel_index: int, params: Adc4DChannelParams):
        self.module = module
        self.channel_index = channel_index
        self.attribute_name = params.attribute_name

    def get_voltage(self) -> float:  # type: ignore[override]
        return float(self.module.read_diff(self.channel_index))


"""
Adc4DParams stores the 'path' to any particular channels in use.

If a channel is to be used in an experiment, Adc4DParams will hold an entry in the
channels mapping (keyed by hardware channel index), and that Adc4DChannelParams
entry will be given an attribute_name string.
"""


class Adc4DParams(ChannelsLike, SlotLike, ChildParams["Adc4D"]):
    type: Literal["adc4D"] = "adc4D"
    name: str = "Adc4D"
    num_channels: ClassVar[int] = 4
    channels: dict[int, Adc4DChannelParams] = Field(default_factory=dict)

    @property
    def inst(self):  # type: ignore[override]
        return Adc4D


class Adc4D(Child[Any, Adc4DParams], ChannelProvider[Adc4DChannel]):
    def __init__(self, module: Any, params: Adc4DParams):
        self.module = module
        self.params = params
        self.channels: list[Adc4DChannel] = [
            Adc4DChannel(module, i, params.channels.get(i, Adc4DChannelParams()))
            for i in range(params.num_channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @property
    def dep(self) -> Any:
        return self.module

    def __str__(self) -> str:
        return f"Adc4D (Slot {self.params.slot}): {len(self.channels)} channels"
