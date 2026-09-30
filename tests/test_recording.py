"""Recording a run from plain Python: ``record_run``, and ``measure(resources, run)``.

A row is one ``run.row(...)`` call, carrying the parameters bound around it by
``run.at(...)``. The rows land in the lab database exactly as a procedure's do.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from pydantic import BaseModel

from lab_procedure import Status
from lab_wizard.lib.custom_measurements import load_custom_measurement
from lab_wizard.lib.data import find, load_plot
from lab_wizard.lib.recording import MeasureStep, Recording, RunStopped, record_run
from lab_wizard.lib.task_adapters.run import run_procedure


def test_each_row_call_is_one_row_with_the_parameters_bound_around_it(tmp_path: Path):
    db = tmp_path / "lab.db"
    with record_run(
        "trigger_scan", database=db, device="A7", params={"gate_s": 0.1},
        units={"bias_voltage": "V"}, plots=[{"name": "Counts", "x": "bias_voltage", "y": ["counts"], "series": "trigger_mV"}],
    ) as run:
        for trigger in (10, 20):
            with run.at(trigger_mV=trigger):
                for bias in (0.1, 0.2):
                    with run.at(bias_voltage=bias):
                        run.row(counts=trigger * bias)
                        run.row(counts=trigger * bias + 1)  # a second row at the same point: its own row

    runs = find(db=db)
    (summary,) = runs.table().to_dicts()
    assert (summary["procedure"], summary["status"], summary["device"], summary["points"]) == ("trigger_scan", "success", "A7", 8)
    points = runs.points()
    assert points.columns[3:] == ["bias_voltage", "trigger_mV", "counts"]
    assert points.select("trigger_mV", "bias_voltage", "counts").rows()[:2] == [(10, 0.1, 1.0), (10, 0.1, 2.0)]
    info = runs.info(runs.ids[0])
    assert info["params"] == {"gate_s": 0.1}
    assert info["columns"]["bias_voltage"] == {"unit": "V"}

    # The Data page draws it with the plots it recorded: one line per trigger.
    spec = {**info["definition"]["plots"][0], "runs": runs.ids}
    assert sorted(load_plot(spec, db)["series"].unique().to_list()) == ["trigger_mV = 10", "trigger_mV = 20"]


def test_a_value_named_like_a_parameter_in_force_is_refused(tmp_path: Path):
    with pytest.raises(ValueError, match="already a parameter in force"):
        with record_run("probe", database=tmp_path / "lab.db") as run:
            with run.at(bias_voltage=0.1):
                run.row(bias_voltage=0.2)
    assert find(db=tmp_path / "lab.db").table()["status"].to_list() == ["failed"]


def test_an_error_fails_the_run_and_a_ctrl_c_aborts_it(tmp_path: Path):
    db = tmp_path / "lab.db"
    with pytest.raises(RuntimeError):
        with record_run("probe", database=db) as run:
            run.row(counts=1)
            raise RuntimeError("counter timed out")
    with pytest.raises(KeyboardInterrupt):
        with record_run("probe", database=db) as run:
            run.row(counts=2)
            raise KeyboardInterrupt
    runs = find(db=db)
    assert runs.table()["status"].to_list() == ["aborted", "failed"]  # newest first
    # What was recorded before it stopped is kept, and the timeline says why.
    assert runs.table()["points"].to_list() == [1, 1]
    steps = runs.steps().to_dicts()
    assert [(s["kind"], s["status"]) for s in steps] == [("script", "failed"), ("script", "aborted")]
    assert "counter timed out" in steps[0]["error"]


# --------------------------- a custom measurement written as a loop ---------------------------


class Params(BaseModel):
    values: list[float] = [1.0, 2.0, 3.0]


@dataclass
class Resources:
    params: Params = field(default_factory=Params)


def _project(tmp_path: Path) -> Path:
    project_dir = tmp_path / "loop_1"
    project_dir.mkdir()
    (project_dir / "loop_1.yaml").write_text("project: {measurement_type: loop}\noutputs: {files: false}\n", encoding="utf-8")
    return project_dir


def test_a_measure_function_runs_as_a_step_and_is_recorded(tmp_path: Path):
    def measure(resources: Resources, run: Recording) -> None:
        for i, value in enumerate(resources.params.values):
            with run.at(index=i):
                run.row(value=value)
            if run.latest["value"] >= 2.0:
                break

    project_dir = _project(tmp_path)
    status = run_procedure(MeasureStep(measure, Resources()), Resources(), procedure="loop", project_dir=project_dir)
    assert status is Status.SUCCESS
    points = find(db=project_dir / "data" / "lab.db").points()
    assert points.select("index", "value").rows() == [(0, 1.0), (1, 2.0)]


def test_run_sleep_notices_a_stop_from_another_thread():
    started = threading.Event()

    def measure(resources: Resources, run: Recording) -> None:
        started.set()
        run.sleep(30)

    step = MeasureStep(measure, Resources())
    from lab_procedure import ProcedureRunner

    runner = ProcedureRunner()
    outcome: list[BaseException] = []

    def go() -> None:
        try:
            runner.run(step)
        except RunStopped as e:
            outcome.append(e)

    thread = threading.Thread(target=go)
    thread.start()
    assert started.wait(5)
    runner.abort()
    thread.join(5)
    assert not thread.is_alive() and outcome and runner.status is Status.ABORTED


def test_a_measurement_file_may_define_measure_instead_of_build_procedure(tmp_path: Path):
    path = tmp_path / "ramp.py"
    path.write_text(
        '"""Ramp a value."""\n'
        "from dataclasses import dataclass, field\n"
        "from pydantic import BaseModel\n"
        "from lab_wizard.lib.instruments.general.vsource import VSource\n"
        "class Params(BaseModel):\n"
        "    n: int = 2\n"
        "@dataclass(frozen=True)\n"
        "class Resources:\n"
        "    source: VSource\n"
        "    params: Params = field(default_factory=Params)\n"
        "def measure(resources, run):\n"
        "    for i in range(resources.params.n):\n"
        "        run.row(i=i)\n",
        encoding="utf-8",
    )
    found = load_custom_measurement(path)
    assert found.description == "Ramp a value." and set(found.roles) == {"source"}
    assert found.entry == "measure"

    # Resources must be frozen: a project's setup narrows its fields in a subclass.
    path.write_text(path.read_text().replace("@dataclass(frozen=True)", "@dataclass"), encoding="utf-8")
    with pytest.raises(ValueError, match=r"frozen=True"):
        load_custom_measurement(path)
    path.write_text(path.read_text().replace("@dataclass\n", "@dataclass(frozen=True)\n"), encoding="utf-8")

    path.write_text(path.read_text() + "def build_procedure(resources):\n    pass\n", encoding="utf-8")
    with pytest.raises(ValueError, match="both measure and build_procedure"):
        load_custom_measurement(path)
