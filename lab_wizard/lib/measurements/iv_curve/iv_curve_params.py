"""Typed measurement parameters for the IV-curve measurement.

This is the single source of truth for what lives under ``measurement.params``
in an IV-curve project YAML. The wizard derives the YAML defaults from these
models, and the generated setup file validates the YAML back into them with
``IVCurveParams.model_validate(project.measurement.params)`` — so the config and
the schema cannot drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lab_wizard.lib.measurements.general.sweep_params import (
    LinearSweepParams,
    SweepParams,
)


class IVBiasParams(BaseModel):
    """How the bias source is swept."""

    sweep: SweepParams = Field(
        default_factory=lambda: LinearSweepParams(start_V=0.0, stop_V=1.4, step_V=0.005)
    )
    settle_s: float = 0.05


class IVReadoutParams(BaseModel):
    """How current is inferred from the sensed voltage."""

    bias_resistance_ohm: float = 100_000.0


class IVSafetyParams(BaseModel):
    """Source on/off and return-to-zero behavior around the sweep."""

    turn_on_at_start: bool = True
    return_to_zero: bool = True
    turn_off_at_end: bool = True


class IVCurveParams(BaseModel):
    bias: IVBiasParams = Field(default_factory=IVBiasParams)
    readout: IVReadoutParams = Field(default_factory=IVReadoutParams)
    safety: IVSafetyParams = Field(default_factory=IVSafetyParams)
