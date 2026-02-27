from __future__ import annotations
from pydantic import BaseModel, Field
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900ChildDep
from lab_wizard.lib.instruments.sim900.deps import Sim900Dep
import time
import numpy as np
from typing import Literal, Any, cast, TypeVar

TChild = TypeVar("TChild", bound=Child[Sim900Dep, Any])


class Sim970ChannelParams(BaseModel):
    """Per-channel configuration for a single SIM970 voltmeter channel."""

    attribute_name: str = ""
    settling_time: float = 0.1
    max_retries: int = 3


class Sim970Params(SlotLike, ChildParams["Sim970"]):
    """Parameters for SIM970 module.

    Per-channel settings (settling time, max retries, attribute name) live in
    each entry of ``channels``. The number of channels is derived from the
    length of that list.
    """

    type: Literal["sim970"] = "sim970"
    slot: int = 0
    attribute_name: str = "Sim970"
    offline: bool | None = False
    channels: list[Sim970ChannelParams] = Field(
        default_factory=lambda: [Sim970ChannelParams() for _ in range(4)]
    )

    @property
    def inst(self):  # type: ignore[override]
        return Sim970

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"


class Sim970Channel(VSense):
    """Single SIM970 voltmeter channel implementing the VSense interface."""

    def __init__(
        self, dep: Sim900ChildDep, channel_index: int, params: Sim970ChannelParams
    ):
        self._dep = dep
        self.channel_index = channel_index
        self.settling_time = params.settling_time
        self.max_retries = params.max_retries
        self.attribute_name = params.attribute_name
        self.connected = True

    def disconnect(self) -> bool:  # type: ignore[override]
        self.connected = False
        return True

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


class Sim970(Child[Sim900Dep, Sim970Params], ChannelProvider[Sim970Channel]):
    """SIM970 module representing a multi-channel voltmeter.

    Channels are exposed via the ``channels`` list and created from per-channel
    ``Sim970ChannelParams`` entries in ``Sim970Params.channels``.
    """

    def __init__(
        self, dep: Sim900ChildDep, parent_dep: Sim900Dep, params: Sim970Params
    ):
        self._dep = dep
        self._parent_dep = parent_dep
        self.params = params
        self.connected = True
        self.slot = params.slot
        self.channels: list[Sim970Channel] = []
        for i, ch_params in enumerate(params.channels):
            ch_dep = Sim900ChildDep(
                parent_dep.serial, parent_dep.gpibAddr, i, offline=params.offline
            )
            self.channels.append(Sim970Channel(ch_dep, i, ch_params))

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @classmethod
    def from_params_with_dep(
        cls, parent_dep: Sim900Dep, key: str, params: ChildParams[Any]
    ) -> "Sim970":
        if not isinstance(params, Sim970Params):
            raise TypeError(
                f"Sim970.from_params_with_dep expected Sim970Params, got {type(params).__name__}"
            )
        comm = Sim900ChildDep(
            parent_dep.serial, parent_dep.gpibAddr, int(key), offline=params.offline
        )
        # Align slot number with key if not explicitly set
        params.slot = int(key)
        return cls(comm, parent_dep, params)

    # ---- Parent API ----
    @property
    def dep(self) -> Sim900Dep:  # type: ignore[override]
        return cast(Sim900Dep, self._dep)

    def disconnect(self) -> bool:
        if not self.connected:
            return True
        for ch in self.channels:
            try:
                ch.disconnect()
            except Exception:
                pass
        self.connected = False
        return True

    def __del__(self):
        if hasattr(self, "connected") and self.connected:
            self.disconnect()

    # Backward compatibility helper: allow get_voltage on module returning ch0
    def get_voltage(self) -> float:  # type: ignore[override]
        if not self.channels:
            raise RuntimeError("No channels initialized")
        return self.channels[0].get_voltage()
