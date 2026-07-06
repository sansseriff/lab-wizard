from __future__ import annotations
from pydantic import BaseModel, Field
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.parent_child import (
    Child,
    ChildParams,
    ChannelProvider,
    ChannelsLike,
    SlotLike,
)
from lab_wizard.lib.instruments.sim900.comm import Sim900SlotDep
import time
import numpy as np
from typing import Any, ClassVar, Literal


class Sim970ChannelParams(BaseModel):
    """Per-channel configuration for a single SIM970 voltmeter channel."""

    attribute_name: str = ""
    settling_time: float = Field(
        default=0.1,
        description="(seconds)",
    )
    max_retries: int = 3


class Sim970Params(ChannelsLike, SlotLike, ChildParams["Sim970"]):
    """Parameters for SIM970 module.

    ``slot`` (via SlotLike) holds the physical slot number within the SIM900
    mainframe and participates in hash key derivation.

    Per-channel settings (settling time, max retries, attribute name) live in
    the ``channels`` mapping, keyed by hardware channel index. The hardware
    channel count is ``num_channels``; the mapping may be sparse.
    """

    type: Literal["sim970"] = "sim970"
    attribute_name: str = ""
    offline: bool | None = False
    num_channels: ClassVar[int] = 4
    channels: dict[int, Sim970ChannelParams] = Field(default_factory=dict)

    @property
    def inst(self):  # type: ignore[override]
        return Sim970


class Sim970Channel(VSense):
    """Single SIM970 voltmeter channel implementing the VSense interface."""

    def __init__(
        self, dep: Sim900SlotDep, channel_index: int, params: Sim970ChannelParams
    ):
        self._dep = dep
        self.slot = dep.slot
        self.channel_index = channel_index
        self.settling_time = params.settling_time
        self.max_retries = params.max_retries
        self.attribute_name = params.attribute_name

    def get_voltage(self) -> float:  # type: ignore[override]
        return self._get_voltage_impl(0)

    def _get_voltage_impl(self, recurse: int) -> float:
        if getattr(self._dep, "offline", False):  # offline simulation
            return float(np.random.uniform())
        channel_scpi = self.channel_index + 1  # hardware channels are 1-based
        cmd = f"VOLT? {channel_scpi}"
        volts = self._dep.query(cmd)  # type: ignore[attr-defined]
        time.sleep(self.settling_time)
        volts = self._dep.query(cmd)  # type: ignore[attr-defined]
        try:
            return float(volts)
        except ValueError:
            if recurse < self.max_retries:
                return self._get_voltage_impl(recurse + 1)
            raise ValueError(f"Could not parse voltage reading: {volts}")


class Sim970(Child[Any, Sim970Params], ChannelProvider[Sim970Channel]):
    """SIM970 module representing a multi-channel voltmeter.

    Channels are exposed via the dense ``channels`` list — one per hardware
    channel — created from the sparse per-channel ``Sim970ChannelParams``
    entries in ``Sim970Params.channels`` (defaults for unconfigured indices).

    from_config is inherited from Child base class — no override needed.
    """

    def __init__(self, dep: Sim900SlotDep, params: Sim970Params):
        self._dep = dep
        self.params = params
        self.slot = dep.slot
        self.channels: list[Sim970Channel] = [
            Sim970Channel(dep, i, params.channels.get(i, Sim970ChannelParams()))
            for i in range(params.num_channels)
        ]

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @property
    def dep(self) -> Sim900SlotDep:  # type: ignore[override]
        return self._dep

    # Backward compatibility helper: allow get_voltage on module returning ch0
    def get_voltage(self) -> float:  # type: ignore[override]
        if not self.channels:
            raise RuntimeError("No channels initialized")
        return self.channels[0].get_voltage()
