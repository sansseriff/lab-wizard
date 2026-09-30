"""Sweep a bias voltage and read the voltage across the detector at each step.

An example custom measurement: the smallest complete one, built from steps
lab_wizard already has. Copy this file to start your own. The wizard lists
every ``.py`` file in this folder (except ones starting with ``_``) as a
measurement you can create a project from.

A custom measurement file declares four things:

* ``Params``            its settings, with defaults. They become the project's
                        ``measurement.params``, editable before every run.
* ``Resources``         a frozen dataclass: one field per instrument it needs,
                        typed by the *behavior* it needs (``VSource``, ``VSense``,
                        ``Counter``, ...), not by a model. The wizard offers every
                        configured instrument that has that behavior, and a
                        project's setup file narrows each field to the class of
                        the one chosen.
* ``build_procedure``   the step tree for one run.
* ``PLOTS`` (optional)  how the Data page and the live plot draw a run.

Every run is recorded in the lab database, saved as files if the project asks,
and plotted live, exactly like a composed procedure's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_procedure import Step
from lab_procedure.steps import Sequence, Sweep, Wait
from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.procedures.sweep_params import LinearSweepParams, SweepParams
from lab_wizard.lib.task_adapters.instrument_steps import ReadVoltage, SetVoltage, SourceGuard


class Params(BaseModel):
    """The settings of one run. Every field needs a default."""

    bias: SweepParams = Field(
        default_factory=lambda: LinearSweepParams(start=0.0, stop=1.0, step=0.05),
        description="the bias voltages to visit, in volts",
    )
    settle_s: float = Field(default=0.05, description="wait after setting each bias before reading, in seconds")


@dataclass(frozen=True)
class Resources:
    """The instruments this measurement needs, and its params."""

    voltage_source: VSource
    voltage_sense: VSense
    params: Params = field(default_factory=Params)


# Plot specs, as in a procedure: columns are the names the steps record.
PLOTS = [
    {"name": "Sensed voltage", "x": "bias_voltage", "y": ["sense_voltage"]},
]


def build_procedure(resources: Resources) -> Step:
    """The step tree for one run.

    ``SourceGuard`` turns the source on, runs its body, and sets it back to
    0 V and off afterwards, even if the run fails or is stopped. ``Sweep``
    binds ``bias_voltage`` for each value, so every reading taken inside is
    recorded with the bias it was taken at.
    """
    params = resources.params
    return SourceGuard(
        source=resources.voltage_source,
        body=Sweep(
            "bias_voltage",
            params.bias.values(),
            lambda bias_voltage: Sequence(
                SetVoltage(source=resources.voltage_source, voltage=bias_voltage),
                Wait(seconds=params.settle_s),
                ReadVoltage(sense=resources.voltage_sense, field="sense_voltage"),
            ),
        ),
    )
