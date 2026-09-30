"""Raise the bias one step at a time until the detector switches, then stop.

An example custom measurement that does what a composed procedure cannot: it
decides as it goes. A sweep visits every value it was given; this stops at the
first reading above a threshold, so a fast search never drives the detector
far past where it switched.

It is written as ``measure(resources, run)``: plain Python, a ``for`` loop and
an ``if``. Three calls on ``run`` are all it needs:

* ``with run.at(bias_voltage=bias):`` — the parameters the rows inside were
  taken at. They are columns like a sweep's, and the Data page can draw or
  filter by them.
* ``run.row(sense_voltage=..., switched=...)`` — record one row. One call is
  one row, always.
* ``run.sleep(seconds)`` — wait, and stop promptly if the run is stopped.

See ``bias_sweep.py`` for what the rest of this file declares, and for a
measurement built from steps instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.recording import Recording


class Params(BaseModel):
    start_v: float = Field(default=0.0, description="the first bias, in volts")
    stop_v: float = Field(default=2.0, description="give up without switching past this bias, in volts")
    step_v: float = Field(default=0.01, gt=0, description="how much to raise the bias each time, in volts")
    threshold_v: float = Field(default=0.01, description="a sensed voltage above this means the detector switched")
    settle_s: float = Field(default=0.02, ge=0, description="wait after each bias before reading, in seconds")


@dataclass(frozen=True)
class Resources:
    voltage_source: VSource
    voltage_sense: VSense
    params: Params = field(default_factory=Params)


PLOTS = [
    {"name": "Ramp", "x": "bias_voltage", "y": ["sense_voltage"]},
]


def measure(resources: Resources, run: Recording) -> None:
    """One run: ramp the bias until the sensed voltage crosses the threshold.

    The ``finally`` puts the source back at 0 V and off however the loop
    ends — switched, gave up, failed, or stopped.
    """
    p = resources.params
    source, sense = resources.voltage_source, resources.voltage_sense
    steps = int(round((p.stop_v - p.start_v) / p.step_v))
    source.set_voltage(p.start_v)
    source.turn_on()
    try:
        for i in range(steps + 1):
            # Computed from the index, so a thousand small steps do not drift.
            bias = p.start_v + i * p.step_v
            source.set_voltage(bias)
            run.sleep(p.settle_s)
            sensed = sense.get_voltage()
            switched = sensed > p.threshold_v
            with run.at(bias_voltage=bias):
                run.row(sense_voltage=sensed, switched=switched)
            if switched:
                break
        # Reaching stop_v without switching is still a complete run: the rows
        # say it never switched.
    finally:
        source.set_voltage(0.0)
        source.turn_off()
