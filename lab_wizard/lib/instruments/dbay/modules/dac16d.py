from typing import Any, Literal, List

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.dbay.addons.vsense import ChSenseState
from lab_wizard.lib.instruments.dbay.addons.vsource import (
    IVsourceAddon,
    VsourceChange,
    SharedVsourceChange,
    ChSourceState,
)
from lab_wizard.lib.instruments.dbay.comm import Comm
from lab_wizard.lib.instruments.dbay.state import Core
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, SlotLike
from lab_wizard.lib.instruments.general.vsource import VSource


# ---------------------- Params & State Models ----------------------


class Dac16DChannelParams(BaseModel):
    """Per-channel configuration for a single Dac16D output channel."""

    attribute_name: str = ""


class Dac16DChannel(VSource):
    """Internal single channel implementation."""

    def __init__(self, comm: Comm, module_slot: int, state: ChSourceState, params: Dac16DChannelParams):
        self.comm = comm
        self.module_slot = module_slot
        self.channel_data = state
        self.channel_index = state.index
        self.attribute_name = params.attribute_name
        self.connected = True

    def disconnect(self) -> bool:  # type: ignore[override]
        if not self.connected:
            return True
        try:
            change = VsourceChange(
                module_index=self.module_slot,
                index=self.channel_index,
                bias_voltage=self.channel_data.bias_voltage,
                activated=self.channel_data.activated,
                heading_text=self.channel_data.heading_text,
                measuring=False,
            )
            self.comm.put("dac16D/vsource/", data=change.model_dump())
        except Exception:
            pass
        self.connected = False
        return True

    def set_voltage(self, voltage: float) -> bool:  # type: ignore[override]
        try:
            change = VsourceChange(
                module_index=self.module_slot,
                index=self.channel_index,
                bias_voltage=voltage,
                activated=self.channel_data.activated,
                heading_text=self.channel_data.heading_text,
                measuring=True,
            )
            self.comm.put("dac16D/vsource/", data=change.model_dump())
            return True
        except Exception as e:
            print(f"Error setting voltage on channel {self.channel_index}: {e}")
            return False

    def turn_on(self) -> bool:  # type: ignore[override]
        try:
            change = VsourceChange(
                module_index=self.module_slot,
                index=self.channel_index,
                bias_voltage=self.channel_data.bias_voltage,
                activated=True,
                heading_text=self.channel_data.heading_text,
                measuring=True,
            )
            self.comm.put("dac16D/vsource/", data=change.model_dump())
            return True
        except Exception as e:
            print(f"Error turning on channel {self.channel_index}: {e}")
            return False

    def turn_off(self) -> bool:  # type: ignore[override]
        try:
            change = VsourceChange(
                module_index=self.module_slot,
                index=self.channel_index,
                bias_voltage=self.channel_data.bias_voltage,
                activated=False,
                heading_text=self.channel_data.heading_text,
                measuring=True,
            )
            self.comm.put("dac16D/vsource/", data=change.model_dump())
            return True
        except Exception as e:
            print(f"Error turning off channel {self.channel_index}: {e}")
            return False


class Dac16DParams(SlotLike, ChildParams["Dac16D"]):
    type: Literal["dac16D"] = "dac16D"
    name: str = "Dac16D"
    channels: list[Dac16DChannelParams] = Field(
        default_factory=lambda: [Dac16DChannelParams() for _ in range(16)]
    )

    @property
    def inst(self):  # type: ignore[override]
        return Dac16D

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"


class Dac16DState(BaseModel):
    module_type: Literal["dac16D"] = "dac16D"
    core: Core
    vsource: IVsourceAddon
    # Additional state fields specific to 16D
    vsb: ChSourceState
    vr: ChSenseState


# ---------------------------- Channel -----------------------------


class Dac16D(Child[Comm, Dac16DParams], ChannelProvider[Dac16DChannel]):
    def __init__(self, data: dict[str, Any], comm: Comm, params: Dac16DParams | None = None):
        self.comm = comm
        self.data = Dac16DState(**data)
        self.core = Core(
            slot=self.data.core.slot, type=self.data.core.type, name=self.data.core.name
        )
        self.params = params if params is not None else Dac16DParams()
        self.connected = True
        # Pair hardware channel states with per-channel params (by index).
        hw_channels = self.data.vsource.channels[: len(self.params.channels)]
        self.channels: list[Dac16DChannel] = [
            Dac16DChannel(self.comm, self.core.slot, st, ch_params)
            for st, ch_params in zip(hw_channels, self.params.channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @classmethod
    def from_params_with_dep(
        cls, parent_dep: Comm, key: str, params: ChildParams[Any]
    ) -> "Dac16D":
        try:
            slot = int(key)
        except ValueError:
            raise TypeError(f"Dac16D requires numeric key for slot, got {key!r}")
        full = parent_dep.get("full-state")
        data_list = full.get("data", [])
        module_info = data_list[slot]
        if module_info["core"]["type"] != "dac16D":
            raise ValueError(
                f"Slot {slot} is not dac16D (found {module_info['core']['type']})"
            )
        dac16d_params = params if isinstance(params, Dac16DParams) else Dac16DParams()
        return cls(module_info, parent_dep, dac16d_params)

    @property
    def dep(self) -> Comm:  # type: ignore[override]
        return self.comm

    def disconnect(self) -> bool:  # type: ignore[override]
        if not self.connected:
            return True
        for ch in getattr(self, "channels", []):
            try:
                ch.disconnect()
            except Exception:
                pass
        self.connected = False
        return True

    def __del__(self):  # pragma: no cover
        if hasattr(self, "connected") and self.connected:
            self.disconnect()

    def __str__(self):
        slot = self.core.slot
        active_channels = sum(1 for ch in self.data.vsource.channels if ch.activated)
        return f"Dac16D (Slot {slot}): {active_channels}/16 channels active"

    # ---- Multi-channel operations ----
    def voltage_set_shared(
        self, voltage: float, activated: bool = True, channels: List[bool] | None = None
    ) -> bool:
        """Set the same voltage to multiple channels at once and silently update children state."""
        if channels is None:
            channels = [True] * 16
        if len(channels) != 16:
            raise ValueError(f"channels mask must be length 16, got {len(channels)}")

        try:
            change = VsourceChange(
                module_index=self.core.slot,
                index=0,  # Index doesn't matter for shared changes
                bias_voltage=voltage,
                activated=activated,
                heading_text=self.data.vsource.channels[0].heading_text,
                measuring=True,
            )

            shared_change = SharedVsourceChange(change=change, link_enabled=channels)

            self.comm.put("dac16D/vsource_shared/", data=shared_change.model_dump())

            # Silent local state update to keep API consistent with backend action
            for i, linked in enumerate(channels):
                if not linked:
                    continue
                # Update cached module state
                ch_state = self.data.vsource.channels[i]
                ch_state.bias_voltage = voltage
                ch_state.activated = activated
                ch_state.measuring = True
                # Update child object if it exists without triggering backend calls
                # Update internal channel objects if present
                if i < len(self.channels):
                    ch_obj = self.channels[i]
                    ch_obj.channel_data.bias_voltage = voltage
                    ch_obj.channel_data.activated = activated
                    ch_obj.channel_data.measuring = True

            return True
        except Exception as e:
            print(f"Error setting shared voltage: {e}")
            return False

    def set_vsb(self, voltage: float, activated: bool = True) -> bool:
        """Set VSB voltage on the module."""
        try:
            change = VsourceChange(
                module_index=self.core.slot,
                index=0,
                bias_voltage=voltage,
                activated=activated,
                heading_text="VSB",
                measuring=True,
            )

            self.comm.put("dac16D/vsb/", data=change.model_dump())
            # Optional: cache VSB state locally
            self.data.vsb.bias_voltage = voltage
            self.data.vsb.activated = activated
            self.data.vsb.measuring = True
            return True
        except Exception as e:
            print(f"Error setting VSB voltage: {e}")
            return False
