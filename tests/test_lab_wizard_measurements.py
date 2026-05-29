from __future__ import annotations

import time

from lab_procedure import ProcedureRunner, Status

from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.vsense import StandInVSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.measurements.iv_curve.iv_curve import (
    IVCurveMeasurement,
    build_iv_procedure,
)
from lab_wizard.lib.measurements.iv_curve.iv_curve_params import (
    IVBiasParams,
    IVCurveParams,
    IVReadoutParams,
    IVSafetyParams,
)
from lab_wizard.lib.measurements.iv_curve.iv_curve_setup_template import IVCurveResources
from lab_wizard.lib.measurements.pcr_curve.pcr_curve import PCRCurve
from lab_wizard.lib.measurements.pcr_curve.pcr_curve_params import (
    PCRBiasParams,
    PCRCurveParams,
    PCRReadoutParams,
)
from lab_wizard.lib.measurements.pcr_curve.pcr_curve_setup_template import (
    PCRCurveResources,
)
from lab_wizard.lib.measurements.general.sweep_params import ExplicitSweepParams
from lab_wizard.lib.savers.saver import StandInSaver


class FakeCounter(Counter):
    """Counter that returns a fixed number of counts per gate."""

    def __init__(self, counts: int) -> None:
        self.counts = counts
        self.gate_time = 1.0

    def count(self, gate_time: float = 1.0, channel: int | None = None) -> int:
        return self.counts

    def set_gate_time(self, gate_time: float, channel: int | None = None) -> bool:
        self.gate_time = gate_time
        return True


def _iv_resources(points: list[float], *, settle_s: float = 0.0) -> IVCurveResources:
    sense = StandInVSense()
    sense.measurement_value = 0.05
    return IVCurveResources(
        savers=[StandInSaver()],
        plotters=[],
        voltage_source=StandInVSource(),
        voltage_sense=sense,
        params=IVCurveParams(
            bias=IVBiasParams(
                sweep=ExplicitSweepParams(values_V=points), settle_s=settle_s
            ),
            readout=IVReadoutParams(bias_resistance_ohm=100_000.0),
            safety=IVSafetyParams(),
        ),
    )


def test_iv_curve_emits_one_observation_per_point_and_shuts_down() -> None:
    points = [0.0, 0.1, 0.2]
    resources = _iv_resources(points)
    saver = resources.savers[0]
    assert isinstance(saver, StandInSaver)

    status = IVCurveMeasurement(resources).run_measurement()

    assert status is Status.SUCCESS
    assert saver.started and saver.ended
    assert saver.run_info is not None and saver.run_info["run_type"] == "iv_curve"

    rows = saver.measurements
    assert len(rows) == len(points)
    for row, bias in zip(rows, points):
        data = row["data"]
        assert data["bias_voltage"] == bias
        assert data["sense_voltage"] == 0.05
        expected_current = (bias - 0.05) / 100_000.0
        assert data["current"] == expected_current
        assert row["metadata"]["bias_voltage"] == bias

    # Source returned to zero and turned off in cleanup.
    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False


def test_iv_curve_abort_runs_safe_shutdown() -> None:
    resources = _iv_resources([0.0, 0.1], settle_s=10.0)
    runner = ProcedureRunner(instruments=resources)
    thread = runner.start(build_iv_procedure(resources))

    time.sleep(0.05)
    runner.abort()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert runner.status is Status.ABORTED
    # on_exit cleanup still ran despite the abort.
    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False


def test_pcr_curve_emits_count_rate_per_point() -> None:
    points = [0.0, 0.5, 1.0]
    gate_time = 2.0
    counts = 1000
    resources = PCRCurveResources(
        savers=[StandInSaver()],
        plotters=[],
        voltage_source=StandInVSource(),
        counter=FakeCounter(counts),
        params=PCRCurveParams(
            bias=PCRBiasParams(
                sweep=ExplicitSweepParams(values_V=points), settle_s=0.0
            ),
            readout=PCRReadoutParams(gate_time_s=gate_time),
        ),
    )
    saver = resources.savers[0]
    assert isinstance(saver, StandInSaver)

    status = PCRCurve(resources).run_measurement()

    assert status is Status.SUCCESS
    rows = saver.measurements
    assert len(rows) == len(points)
    for row, bias in zip(rows, points):
        data = row["data"]
        assert data["bias_voltage"] == bias
        assert data["counts"] == counts
        assert data["int_time"] == gate_time
        assert data["count_rate"] == counts / gate_time
        # SaverSink maps counts/int_time into the dedicated saver columns too.
        assert row["counts"] == counts
        assert row["int_time"] == gate_time

    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False
