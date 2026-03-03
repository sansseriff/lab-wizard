"""
keysight53220A.py

Keysight 53220A Universal Counter.

Structured as:
  - Keysight53220AChannelParams: per-channel configuration
  - Keysight53220AParams: standalone top-level params with CanInstantiate
  - Keysight53220AChannel: per-channel Counter implementation
  - Keysight53220A: instrument with ChannelProvider
"""
from __future__ import annotations

import random
from typing import Literal

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.visa import VisaDep, LocalVisaDep
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.parent_child import (
    Instrument,
    CanInstantiate,
    ChannelProvider,
    IPLike,
)


class Keysight53220AChannelParams(BaseModel):
    """Per-channel configuration for the Keysight 53220A."""

    attribute_name: str = ""
    threshold_type: str = "absolute"
    threshold_absolute: float = -50.0
    gate_time: float = 1.0
    trigger_slope: str = "positive"
    input_coupling: str = "DC"
    input_impedance: str = "50"


class Keysight53220AChannel(Counter):
    """Single input channel on the Keysight 53220A (internal, no params).

    Receives a shared VisaDep from the parent instrument and addresses
    hardware using 1-based SCPI channel syntax (@1, @2).
    """

    def __init__(
        self,
        dep: VisaDep,
        channel_index: int,
        offline: bool,
        params: Keysight53220AChannelParams,
    ):
        self._dep = dep
        self.channel_index = channel_index
        self._scpi_ch = channel_index + 1  # hardware is 1-based
        self.offline = offline
        self._gate_time = params.gate_time
        self._threshold = params.threshold_absolute
        self.attribute_name = params.attribute_name
        self.connected = True

    def disconnect(self) -> bool:
        self.connected = False
        return True

    def count(self, gate_time: float = 1.0, channel: int | None = None) -> int:
        if abs(gate_time - self._gate_time) > 0.001:
            self.set_gate_time(gate_time)

        frequency = self.read_counts()
        return int(frequency * gate_time)

    def read_counts(self) -> float:
        """Read a single count/frequency measurement on this channel."""
        if self.offline:
            return float(random.randint(1000, 10000))

        values_str = self._dep.query(f"MEAS:FREQ? (@{self._scpi_ch})")
        if not values_str or values_str == "":
            return 0.0

        return float(str(values_str).split(",")[0])

    def set_gate_time(self, gate_time: float, channel: int | None = None) -> bool:
        if self.offline:
            self._gate_time = gate_time
            return True

        self._dep.write(f"SENS:FREQ:GATE:TIME {gate_time}")
        self._gate_time = gate_time
        return True

    def set_threshold(self, threshold: float) -> bool:
        """Set trigger threshold in mV for this channel."""
        if self.offline:
            self._threshold = threshold
            return True

        threshold_v = threshold / 1000.0
        self._dep.write(f"INP{self._scpi_ch}:LEV {threshold_v}")
        self._threshold = threshold
        return True


class Keysight53220AParams(IPLike, BaseModel, CanInstantiate["Keysight53220A"]):
    """Parameters for Keysight 53220A universal counter.

    Standalone top-level instrument connected via VISA (TCP/IP).
    Per-channel settings (threshold, gate time, trigger slope, etc.) live in
    each entry of ``channels``.
    """

    type: Literal["keysight53220A"] = "keysight53220A"
    ip_address: str = "10.7.0.114"
    ip_port: int = 5025
    offline: bool = False
    ext_trigger: bool = False
    channels: list[Keysight53220AChannelParams] = Field(
        default_factory=lambda: [
            Keysight53220AChannelParams(),
            Keysight53220AChannelParams(),
        ]
    )

    @property
    def inst(self) -> type[Keysight53220A]:
        return Keysight53220A

    def create_inst(self) -> Keysight53220A:
        return Keysight53220A.from_params(self)

    def __call__(self) -> Keysight53220A:
        return self.create_inst()


class Keysight53220A(Instrument, ChannelProvider[Keysight53220AChannel]):
    """Keysight 53220A Universal Counter.

    Uses LocalVisaDep for VISA communication. Channels are exposed via
    the ChannelProvider interface (e.g., inst[0].count(), inst[1].read_counts()).
    """

    def __init__(self, dep: VisaDep, params: Keysight53220AParams):
        self._dep = dep
        self.params = params
        self.connected = not params.offline

        self.channels: list[Keysight53220AChannel] = [
            Keysight53220AChannel(
                dep=dep,
                channel_index=i,
                offline=params.offline,
                params=ch_params,
            )
            for i, ch_params in enumerate(params.channels)
        ]

        if self.connected:
            self._apply_configuration()

    @classmethod
    def from_params(cls, params: Keysight53220AParams) -> Keysight53220A:
        resource = f"TCPIP::{params.ip_address}::{params.ip_port}::SOCKET"
        dep = LocalVisaDep(resource=resource, timeout=5.0)
        return cls(dep, params)

    def _apply_configuration(self) -> bool:
        if self.params.offline:
            return True

        success = True
        for ch, ch_params in zip(self.channels, self.params.channels):
            if ch_params.threshold_type == "absolute":
                success &= ch.set_threshold(ch_params.threshold_absolute)
            success &= ch.set_gate_time(ch_params.gate_time)

        return success

    def disconnect(self) -> bool:
        for ch in self.channels:
            ch.disconnect()
        try:
            self._dep.close()
        except Exception:
            pass
        self.connected = False
        return True

    def reset(self) -> bool:
        if self.params.offline:
            return True
        self._dep.write("*RST")
        return True

    def __del__(self):
        if hasattr(self, "connected") and self.connected:
            self.disconnect()
