"""The built-in iv_curve and pcr_curve procedures, run against stand-in instruments.

Each procedure's generated module is built in memory, exactly as a project
would get it, and run with stand-ins, so these tests check what the procedure
does with a reading rather than detector physics. The simulated detector is
exercised in ``test_iv_curve_end_to_end.py`` and ``test_pcr_curve_end_to_end.py``.
"""

from __future__ import annotations

import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from lab_procedure import ProcedureRunner, Status

from lab_wizard.lib.project_module import module_path
from lab_wizard.lib.data import find
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.vsense import StandInVSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.procedures.codegen import measurement_module_source
from lab_wizard.lib.procedures.storage import load_procedure


class StubCounter(Counter):
    """Counter that returns a fixed number of counts per gate.

    A stub, not a simulation: it exists to check what the procedure does with a
    count. The simulated counter that produces counts from detector physics is
    the ``keysight53220A`` driver against ``lab_sim``'s simulated bench.
    """

    def __init__(self, counts: int) -> None:
        self.counts = counts
        self.gate_time = 1.0
        self.threshold_mV = 0.0

    def count(self, gate_time: float | None = None) -> int:
        return self.counts

    def set_gate_time(self, gate_time: float) -> bool:
        self.gate_time = gate_time
        return True

    def get_gate_time(self) -> float:
        return self.gate_time

    def set_threshold(self, threshold_mV: float) -> bool:
        self.threshold_mV = threshold_mV
        return True

    def get_threshold(self) -> float:
        return self.threshold_mV


@dataclass
class Resources:
    params: Any
    voltage_source: Any = None
    voltage_sense: Any = None
    counter: Any = None


def _module(name: str, project_dir: Path) -> types.ModuleType:
    """The procedure's generated module, as if it sat in ``project_dir``."""
    definition = load_procedure(project_dir / "no-config", name)
    module = types.ModuleType(name)
    module.__file__ = str(module_path(project_dir, name))  # where it records its runs
    exec(compile(measurement_module_source(definition), module.__file__, "exec"), module.__dict__)
    return module


def _params(name: str, values: dict[str, Any]) -> Any:
    return load_procedure(Path("no-config"), name).params_model().model_validate(values)


def _iv_resources(points: list[float], *, settle_s: float = 0.0) -> Resources:
    sense = StandInVSense()
    sense.measurement_value = 0.05
    return Resources(
        voltage_source=StandInVSource(),
        voltage_sense=sense,
        params=_params("iv_curve", {
            "bias": {"sweep": {"mode": "explicit", "values": points}, "settle_s": settle_s},
            "readout": {"bias_resistance_ohm": 100_000.0},
        }),
    )


def test_iv_curve_records_one_row_per_point_and_shuts_down(tmp_path: Path) -> None:
    points = [0.0, 0.1, 0.2]
    resources = _iv_resources(points)
    module = _module("iv_curve", tmp_path)

    status = module.IvCurveMeasurement(resources).run_measurement()

    assert status is Status.SUCCESS
    runs = find(db=tmp_path / "data" / "lab.db")
    assert runs.table().select("procedure", "status").rows() == [("iv_curve", "success")]
    rows = runs.points(runs.derived())
    assert list(zip(rows["bias_voltage"], rows["sense_voltage"])) == [(bias, 0.05) for bias in points]

    # The current is derived when the run is read, from its own bias resistance.
    assert rows["current"].to_list() == pytest.approx([(bias - 0.05) / 100_000.0 for bias in points])

    # Source returned to zero and turned off in cleanup.
    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False


def test_iv_curve_abort_runs_safe_shutdown(tmp_path: Path) -> None:
    resources = _iv_resources([0.0, 0.1], settle_s=10.0)
    runner = ProcedureRunner(instruments=resources)
    thread = runner.start(_module("iv_curve", tmp_path).build_iv_curve_procedure(resources))

    time.sleep(0.05)
    runner.abort()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert runner.status is Status.ABORTED
    # on_exit cleanup still ran despite the abort.
    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False


def test_pcr_curve_records_a_count_rate_per_point(tmp_path: Path) -> None:
    points = [0.0, 0.5, 1.0]
    gate_time = 2.0
    counts = 1000
    resources = Resources(
        voltage_source=StandInVSource(),
        counter=StubCounter(counts),
        params=_params("pcr_curve", {
            "bias": {"sweep": {"mode": "explicit", "values": points}, "settle_s": 0.0},
            "readout": {"gate_time_s": gate_time, "threshold_mV": -40.0},
        }),
    )

    status = _module("pcr_curve", tmp_path).PcrCurveMeasurement(resources).run_measurement()

    assert status is Status.SUCCESS
    assert resources.counter.threshold_mV == -40.0  # set by the run, not inherited
    rows = find(db=tmp_path / "data" / "lab.db").points()
    assert rows.select("bias_voltage", "counts", "int_time", "count_rate").rows(named=True) == [
        {"bias_voltage": bias, "counts": counts, "int_time": gate_time, "count_rate": counts / gate_time}
        for bias in points
    ]

    assert resources.voltage_source.voltage == 0.0
    assert resources.voltage_source.output_enabled is False
