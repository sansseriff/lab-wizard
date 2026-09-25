"""Schemas for steps that drive instruments through their behaviors."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from lab_wizard.lib.procedures.spec import AnyStep, Requires, RoleRef, StepClass, StepParams, Value
from lab_wizard.lib.task_adapters.instrument_steps import (
    CloseShutter,
    Count,
    LaserOff,
    LaserOn,
    OpenShutter,
    ReadVoltage,
    ReturnToZeroAndOff,
    SafeGuard,
    SetAttenuation,
    SetLaserPower,
    SetThreshold,
    SetVoltage,
    SourceGuard,
    TurnOn,
    WithSettings,
)

# Behaviors that declare a safe state, for the guard.
_HAS_SAFE_STATE = ("VSource", "Attenuator", "Laser")


class SetVoltageStepParams(StepParams):
    """Set a source's output voltage."""

    type: Literal["set_voltage"] = "set_voltage"
    source: Annotated[RoleRef, Requires("VSource")]
    voltage: Value

    @classmethod
    def step_class(cls) -> StepClass:
        return SetVoltage


class TurnOnStepParams(StepParams):
    """Enable a source's output."""

    type: Literal["turn_on"] = "turn_on"
    source: Annotated[RoleRef, Requires("VSource")]

    @classmethod
    def step_class(cls) -> StepClass:
        return TurnOn


class ReturnToZeroAndOffStepParams(StepParams):
    """Drive a source to 0 V and/or turn it off."""

    type: Literal["return_to_zero_and_off"] = "return_to_zero_and_off"
    source: Annotated[RoleRef, Requires("VSource")]
    return_to_zero: bool = True
    turn_off: bool = True

    @classmethod
    def step_class(cls) -> StepClass:
        return ReturnToZeroAndOff


class SourceGuardStepParams(StepParams):
    """Run ``body`` with a source on, guaranteeing it is returned to 0 V and off."""

    type: Literal["source_guard"] = "source_guard"
    source: Annotated[RoleRef, Requires("VSource")]
    body: AnyStep
    turn_on_at_start: Value = True
    return_to_zero: Value = True
    turn_off_at_end: Value = True

    @classmethod
    def step_class(cls) -> StepClass:
        return SourceGuard


class SafeGuardStepParams(StepParams):
    """Run ``body``, then put the instrument into its declared safe state — always."""

    type: Literal["safe_guard"] = "safe_guard"
    instrument: Annotated[RoleRef, Requires(*_HAS_SAFE_STATE)]
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return SafeGuard


class WithSettingsStepParams(StepParams):
    """Run ``body`` with settings overridden, restoring them afterwards.

    ``overrides`` maps a setting to its value for the body: ``threshold`` means
    the instrument's ``set_threshold`` / ``get_threshold``.
    """

    type: Literal["with_settings"] = "with_settings"
    instrument: RoleRef
    overrides: dict[str, Value] = Field(default_factory=dict)
    body: AnyStep

    @classmethod
    def step_class(cls) -> StepClass:
        return WithSettings


class SetThresholdStepParams(StepParams):
    """Set a counter's discriminator threshold, in millivolts."""

    type: Literal["set_threshold"] = "set_threshold"
    counter: Annotated[RoleRef, Requires("Counter")]
    threshold_mV: Value

    # Record what the instrument reached under this name, beside the value asked for.
    record: str | None = Field(default=None, json_schema_extra={"column": "records"})

    @classmethod
    def step_class(cls) -> StepClass:
        return SetThreshold

    def emitted_fields(self) -> tuple[str, ...]:
        return (self.record,) if self.record else ()

    def emitted_units(self) -> dict[str, str | None]:
        return {self.record: "mV"} if self.record else {}


class CountStepParams(StepParams):
    """Count for one gate; records counts, int_time and count_rate."""

    type: Literal["count"] = "count"
    counter: Annotated[RoleRef, Requires("Counter")]
    gate_time: Value

    emits = ("counts", "int_time", "count_rate")
    units = {"int_time": "s", "count_rate": "Hz"}

    @classmethod
    def step_class(cls) -> StepClass:
        return Count


class ReadVoltageStepParams(StepParams):
    """Read a voltmeter; records the reading under ``field``."""

    type: Literal["read_voltage"] = "read_voltage"
    sense: Annotated[RoleRef, Requires("VSense")]
    field: str = Field(default="voltage", json_schema_extra={"column": "records"})

    @classmethod
    def step_class(cls) -> StepClass:
        return ReadVoltage

    def emitted_fields(self) -> tuple[str, ...]:
        return (self.field,)

    def emitted_units(self) -> dict[str, str | None]:
        return {self.field: "V"}


class SetAttenuationStepParams(StepParams):
    """Set an attenuator's attenuation, in dB."""

    type: Literal["set_attenuation"] = "set_attenuation"
    attenuator: Annotated[RoleRef, Requires("Attenuator")]
    attenuation_db: Value

    # Record what the instrument reached under this name, beside the value asked for.
    record: str | None = Field(default=None, json_schema_extra={"column": "records"})

    @classmethod
    def step_class(cls) -> StepClass:
        return SetAttenuation

    def emitted_fields(self) -> tuple[str, ...]:
        return (self.record,) if self.record else ()

    def emitted_units(self) -> dict[str, str | None]:
        return {self.record: "dB"} if self.record else {}


class OpenShutterStepParams(StepParams):
    """Let light through an attenuator."""

    type: Literal["open_shutter"] = "open_shutter"
    attenuator: Annotated[RoleRef, Requires("Attenuator")]

    @classmethod
    def step_class(cls) -> StepClass:
        return OpenShutter


class CloseShutterStepParams(StepParams):
    """Block light through an attenuator."""

    type: Literal["close_shutter"] = "close_shutter"
    attenuator: Annotated[RoleRef, Requires("Attenuator")]

    @classmethod
    def step_class(cls) -> StepClass:
        return CloseShutter


class LaserOnStepParams(StepParams):
    """Start a laser emitting."""

    type: Literal["laser_on"] = "laser_on"
    laser: Annotated[RoleRef, Requires("Laser")]

    @classmethod
    def step_class(cls) -> StepClass:
        return LaserOn


class LaserOffStepParams(StepParams):
    """Stop a laser emitting."""

    type: Literal["laser_off"] = "laser_off"
    laser: Annotated[RoleRef, Requires("Laser")]

    @classmethod
    def step_class(cls) -> StepClass:
        return LaserOff


class SetLaserPowerStepParams(StepParams):
    """Set a laser's output power, in dBm."""

    type: Literal["set_laser_power"] = "set_laser_power"
    laser: Annotated[RoleRef, Requires("Laser")]
    power_dbm: Value

    # Record what the instrument reached under this name, beside the value asked for.
    record: str | None = Field(default=None, json_schema_extra={"column": "records"})

    @classmethod
    def step_class(cls) -> StepClass:
        return SetLaserPower

    def emitted_fields(self) -> tuple[str, ...]:
        return (self.record,) if self.record else ()

    def emitted_units(self) -> dict[str, str | None]:
        return {self.record: "dBm"} if self.record else {}
