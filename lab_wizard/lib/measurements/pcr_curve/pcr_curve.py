"""PCR (photon-count-rate) measurement built on the ``lab_procedure`` framework.

The measurement is expressed as a ``Step`` tree: turn the source on, sweep the
bias voltage (set, settle, count), then return to zero / turn off. Each bias
point emits an :class:`~lab_procedure.Observation` on the run's ``data_bus``;
savers and plotters consume that stream via
:class:`~lab_wizard.lib.task_adapters.savers.SaverSink` and
:class:`~lab_wizard.lib.task_adapters.plotters.PlotterSink`.

Configuration comes from the typed
:class:`~lab_wizard.lib.measurements.pcr_curve.pcr_curve_params.PCRCurveParams`
(``bias`` / ``readout``), validated from the project YAML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lab_procedure import (
    Observation,
    ProcedureRunner,
    RunEnded,
    RunStarted,
    Sequence,
    Status,
    Step,
    Sweep,
    Wait,
)

from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.task_adapters import PlotterSink, SaverSink
from lab_wizard.lib.task_adapters.instrument_steps import (
    SetVoltage,
    SourceGuard,
)

if TYPE_CHECKING:
    from lab_wizard.lib.measurements.pcr_curve.pcr_curve_setup_template import (
        PCRCurveResources,
    )


class CountAtBias(Step):
    """Count for ``gate_time`` seconds at a bias point and emit one observation."""

    def __init__(
        self,
        counter: Counter,
        bias_voltage: float,
        gate_time: float,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.counter = counter
        self.bias_voltage = bias_voltage
        self.gate_time = gate_time

    def run(self) -> Status:
        assert self.context is not None
        counts = self.counter.count(self.gate_time)
        count_rate = counts / self.gate_time if self.gate_time else 0.0
        self.context.data_bus.emit(
            Observation(
                data={
                    "bias_voltage": self.bias_voltage,
                    "counts": counts,
                    "int_time": self.gate_time,
                    "count_rate": count_rate,
                },
                metadata=self.context.snapshot_parameters(),
                sequence_index=self.context.next_sequence_index(),
                sweep_index=self.context.sweep_index,
            )
        )
        return Status.SUCCESS


def build_pcr_procedure(resources: "PCRCurveResources") -> Step:
    """Build the PCR-curve ``Step`` tree from the resources' params."""
    params = resources.params
    source = resources.voltage_source
    counter = resources.counter

    points = params.bias.sweep.values()
    settle_s = params.bias.settle_s
    gate_time = params.readout.gate_time_s

    def point(bias: object) -> Step:
        bias_v = float(bias)  # type: ignore[arg-type]
        return Sequence(
            SetVoltage(source, bias_v),
            Wait(settle_s),
            CountAtBias(counter, bias_v, gate_time),
            name=f"point({bias_v:g}V)",
        )

    return SourceGuard(
        source,
        Sweep("bias_voltage", points, point, name="bias_sweep"),
        name="pcr_curve",
    )


class PCRCurve:
    """Build, wire, and run a PCR-curve procedure for a set of resources."""

    def __init__(self, resources: "PCRCurveResources") -> None:
        self.resources = resources

    def build_procedure(self) -> Step:
        return build_pcr_procedure(self.resources)

    def run_measurement(self) -> Status:
        runner = ProcedureRunner(instruments=self.resources)
        bus = runner.context.data_bus
        message_types = (RunStarted, Observation, RunEnded)
        bus.subscribe(message_types, SaverSink(self.resources.savers).handle)
        bus.subscribe(message_types, PlotterSink(self.resources.plotters).handle)
        run_started = RunStarted(
            run_type="pcr_curve",
            config=self.resources.params.model_dump(mode="json"),
        )
        return runner.run(self.build_procedure(), run_started)
