"""IV-curve measurement built on the ``lab_procedure`` framework.

The measurement is expressed as a ``Step`` tree: turn the source on, sweep the
bias voltage (set, settle, measure), then return to zero / turn off. Each
measured point is emitted as an :class:`~lab_procedure.Observation` on the
run's ``data_bus``; savers and plotters consume that stream via
:class:`~lab_wizard.lib.task_adapters.savers.SaverSink` and
:class:`~lab_wizard.lib.task_adapters.plotters.PlotterSink`.

Configuration comes from the typed
:class:`~lab_wizard.lib.measurements.iv_curve.iv_curve_params.IVCurveParams`
(``bias`` / ``readout`` / ``safety``), which is validated from the project YAML.
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

from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.task_adapters import PlotterSink, SaverSink
from lab_wizard.lib.task_adapters.instrument_steps import (
    SetVoltage,
    SourceGuard,
)

if TYPE_CHECKING:
    from lab_wizard.lib.measurements.iv_curve.iv_curve_setup_template import (
        IVCurveResources,
    )


class MeasureIVPoint(Step):
    """Read the sense voltage at a bias point and emit one observation.

    Current through the bias resistor is inferred as
    ``(bias_voltage - sense_voltage) / bias_resistance_ohm`` (amps).
    """

    def __init__(
        self,
        voltage_sense: VSense,
        bias_voltage: float,
        bias_resistance_ohm: float,
        name: str | None = None,
    ) -> None:
        super().__init__(name=name)
        self.voltage_sense = voltage_sense
        self.bias_voltage = bias_voltage
        self.bias_resistance_ohm = bias_resistance_ohm

    def run(self) -> Status:
        assert self.context is not None
        sense_voltage = self.voltage_sense.measure()
        current = (self.bias_voltage - sense_voltage) / self.bias_resistance_ohm
        self.context.data_bus.emit(
            Observation(
                data={
                    "bias_voltage": self.bias_voltage,
                    "sense_voltage": sense_voltage,
                    "current": current,
                },
                metadata=self.context.snapshot_parameters(),
                sequence_index=self.context.next_sequence_index(),
                sweep_index=self.context.sweep_index,
            )
        )
        return Status.SUCCESS


def build_iv_procedure(resources: "IVCurveResources") -> Step:
    """Build the IV-curve ``Step`` tree from the resources' params."""
    params = resources.params
    source = resources.voltage_source
    sense = resources.voltage_sense

    points = params.bias.sweep.values()
    settle_s = params.bias.settle_s
    bias_resistance_ohm = params.readout.bias_resistance_ohm

    def point(bias: object) -> Step:
        bias_v = float(bias)  # type: ignore[arg-type]
        return Sequence(
            SetVoltage(source, bias_v),
            Wait(settle_s),
            MeasureIVPoint(sense, bias_v, bias_resistance_ohm),
            name=f"point({bias_v:g}V)",
        )

    return SourceGuard(
        source,
        Sweep("bias_voltage", points, point, name="bias_sweep"),
        turn_on_at_start=params.safety.turn_on_at_start,
        return_to_zero=params.safety.return_to_zero,
        turn_off_at_end=params.safety.turn_off_at_end,
        name="iv_curve",
    )


class IVCurveMeasurement:
    """Build, wire, and run an IV-curve procedure for a set of resources."""

    def __init__(self, resources: "IVCurveResources") -> None:
        self.resources = resources

    def build_procedure(self) -> Step:
        return build_iv_procedure(self.resources)

    def run_measurement(self) -> Status:
        runner = ProcedureRunner(instruments=self.resources)
        bus = runner.context.data_bus
        message_types = (RunStarted, Observation, RunEnded)
        bus.subscribe(message_types, SaverSink(self.resources.savers).handle)
        bus.subscribe(message_types, PlotterSink(self.resources.plotters).handle)
        run_started = RunStarted(
            run_type="iv_curve",
            config=self.resources.params.model_dump(mode="json"),
        )
        return runner.run(self.build_procedure(), run_started)
