"""Typed measurement parameters for the PCR (photon-count-rate) measurement.

Single source of truth for ``measurement.params`` in a PCR-curve project YAML.
The wizard derives the YAML defaults from these models, and the generated setup
file validates the YAML back into them with
``PCRCurveParams.model_validate(project.measurement.params)``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from lab_wizard.lib.measurements.general.sweep_params import (
    LinearSweepParams,
    SweepParams,
)


class PCRBiasParams(BaseModel):
    """How the bias source is swept while counting."""

    sweep: SweepParams = Field(
        default_factory=lambda: LinearSweepParams(start_V=0.0, stop_V=1.0, step_V=0.01)
    )
    settle_s: float = 0.05


class PCRReadoutParams(BaseModel):
    """Counter / illumination settings for the count-rate readout."""

    photon_rate_hz: float = 100_000.0
    gate_time_s: float = 1.0


class PCRCurveParams(BaseModel):
    bias: PCRBiasParams = Field(default_factory=PCRBiasParams)
    readout: PCRReadoutParams = Field(default_factory=PCRReadoutParams)
