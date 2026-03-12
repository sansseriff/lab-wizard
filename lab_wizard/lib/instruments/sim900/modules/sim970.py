from __future__ import annotations
from pydantic import BaseModel, Field
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, ChannelProvider, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900SlotDep
import time
import numpy as np
from typing import Literal, Any, cast, TypeVar

TChild = TypeVar("TChild", bound=Child[Any, Any])


class Sim970ChannelParams(BaseModel):
    """Per-channel configuration for a single SIM970 voltmeter channel."""

    attribute_name: str = ""
    settling_time: float = Field(
        default=0.1,
        description="(seconds)",
    )
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
    channels: list[Sim970ChannelParams] = Field(default_factory=lambda: [Sim970ChannelParams() for _ in range(4)])

    @property
    def inst(self):  # type: ignore[override]
        return Sim970

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"


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


class Sim970(Child[Any, Sim970Params], ChannelProvider[Sim970Channel]):
    """SIM970 module representing a multi-channel voltmeter.

    Channels are exposed via the ``channels`` list and created from per-channel
    ``Sim970ChannelParams`` entries in ``Sim970Params.channels``.
    """

    def __init__(
        self, dep: Sim900SlotDep, params: Sim970Params
    ):
        self._dep = dep
        self.params = params
        self.connected = True
        self.slot = dep.slot
        self.channels: list[Sim970Channel] = []
        for i, ch_params in enumerate(params.channels):
            self.channels.append(Sim970Channel(dep, i, ch_params))

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @classmethod
    def from_config(cls, parent: Any, key: str | int) -> "Sim970":
        norm_key = str(key)
        existing = getattr(parent, "children", {}).get(norm_key)
        if existing is not None:
            if not isinstance(existing, cls):
                raise TypeError(
                    f"Expected Sim970 child at {norm_key!r}, got {type(existing).__name__}"
                )
            return existing

        child_params = parent.params.children[norm_key]
        if not isinstance(child_params, Sim970Params):
            raise TypeError(
                f"Expected Sim970Params at {norm_key!r}, got {type(child_params).__name__}"
            )
        return cast("Sim970", parent.init_child_by_key(norm_key))

    # ---- Parent API ----
    @property
    def dep(self) -> Sim900SlotDep:  # type: ignore[override]
        return self._dep

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
