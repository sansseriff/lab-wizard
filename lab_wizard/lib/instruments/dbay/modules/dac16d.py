from typing import Any, Literal, List
from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, SlotLike
from lab_wizard.lib.instruments.general.vsource import VSource


class Dac16DChannelParams(BaseModel):
    """Per-channel configuration for a single Dac16D output channel."""
    attribute_name: str = Field(default="")


class Dac16DChannel(VSource):
    """Single output channel for Dac16D."""

    def __init__(self, module: Any, channel_index: int, params: Dac16DChannelParams):
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


class Dac16DParams(SlotLike, ChildParams["Dac16D"]):
    type: Literal["dac16D"] = "dac16D"
    name: str = "Dac16D"
    channels: list[Dac16DChannelParams] = Field(default_factory=lambda: [Dac16DChannelParams() for _ in range(16)])

    @property
    def inst(self):  # type: ignore[override]
        return Dac16D


class Dac16D(Child[Any, Dac16DParams], ChannelProvider[Dac16DChannel]):
    def __init__(self, module: Any, params: Dac16DParams):
        self.module = module
        self.params = params
        self.channels: list[Dac16DChannel] = [
            Dac16DChannel(module, i, ch_params)
            for i, ch_params in enumerate(params.channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @property
    def dep(self) -> Any:
        return self.module

    def voltage_set_shared(
        self, voltage: float, activated: bool = True, channels: List[bool] | None = None
    ) -> bool:
        """Set the same voltage to multiple channels at once."""
        try:
            self.module.set_voltage_shared(voltage, activated=activated, channels=channels)
            return True
        except Exception as e:
            print(f"Error setting shared voltage: {e}")
            return False

    def set_vsb(self, voltage: float) -> bool:
        """Set VSB voltage on the module."""
        try:
            self.module.set_bias(voltage)
            return True
        except Exception as e:
            print(f"Error setting VSB voltage: {e}")
            return False

    def __str__(self) -> str:
        return f"Dac16D (Slot {self.params.slot}): {len(self.channels)} channels"
