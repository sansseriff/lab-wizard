"""Raise the bias one step at a time until the detector switches, then stop.

An example custom measurement that does what a composed procedure cannot: it
decides as it goes. A sweep visits every value it was given; this stops at the
first reading above a threshold, so a fast search never drives the detector
far past where it switched.

The decision lives in ``RampUntilSwitch``, a ``Step`` of our own. Its ``run()``
is ordinary Python. Two rules make it behave like every other step:

* record with ``self.context.observe({...})``. Each call that repeats a field
  starts a new row, so one call per bias value is one row per bias value.
* wait with ``self.sleep(seconds)``, not ``time.sleep``. It returns False when
  the run is stopped, so Stop in the wizard and Ctrl-C in a terminal work.

See ``bias_sweep.py`` for what the rest of this file declares.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from lab_procedure import Status, Step
from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.task_adapters.instrument_steps import SourceGuard


class Params(BaseModel):
    start_v: float = Field(default=0.0, description="the first bias, in volts")
    stop_v: float = Field(default=2.0, description="give up without switching past this bias, in volts")
    step_v: float = Field(default=0.01, gt=0, description="how much to raise the bias each time, in volts")
    threshold_v: float = Field(default=0.01, description="a sensed voltage above this means the detector switched")
    settle_s: float = Field(default=0.02, ge=0, description="wait after each bias before reading, in seconds")


@dataclass
class Resources:
    voltage_source: VSource
    voltage_sense: VSense
    params: Params = field(default_factory=Params)


PLOTS = [
    {"name": "Ramp", "x": "bias_voltage", "y": ["sense_voltage"]},
]


class RampUntilSwitch(Step):
    """Raise the bias until the sensed voltage crosses the threshold."""

    def __init__(self, source: VSource, sense: VSense, params: Params, name: str | None = None) -> None:
        super().__init__(name=name)
        self.source = source
        self.sense = sense
        self.params = params

    def run(self) -> Status:
        assert self.context is not None
        p = self.params
        steps = int(round((p.stop_v - p.start_v) / p.step_v))
        for i in range(steps + 1):
            # Computed from the index, so a thousand small steps do not drift.
            bias = p.start_v + i * p.step_v
            self.source.set_voltage(bias)
            if not self.sleep(p.settle_s):
                return Status.ABORTED
            sensed = self.sense.get_voltage()
            switched = sensed > p.threshold_v
            self.context.observe({"bias_voltage": bias, "sense_voltage": sensed, "switched": switched})
            if switched:
                return Status.SUCCESS
        # Reaching stop_v without switching is still a complete run: the rows
        # say it never switched.
        return Status.SUCCESS


def build_procedure(resources: Resources) -> Step:
    return SourceGuard(
        source=resources.voltage_source,
        body=RampUntilSwitch(resources.voltage_source, resources.voltage_sense, resources.params),
    )
