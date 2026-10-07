"""The file saver: each run as a folder of CSV and YAML, beside the lab database.

The central check is that a folder is a complete copy of the run's database
rows: a run saved live and the same run exported from the database afterwards
produce identical files.
"""

from __future__ import annotations

import csv
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from lab_procedure import Point, RunStarted, Sequence, Status, Step, Sweep, WithParameter
from lab_wizard.lib.data import PlotSpec, find, open_database
from lab_wizard.lib.data.plot import default_plot
from lab_wizard.lib.data.run_folder import export_run, folder_name
from lab_wizard.lib.data.settings import DataSettings, FileSettings, save_data_settings
from lab_wizard.lib.data.setups import save_setup
from lab_wizard.lib.plotters import MplPlotter, WebPlotter
from lab_wizard.lib.savers import FileSaver
from lab_wizard.lib.task_adapters.sinks import RunInfo, StandInSink
from lab_wizard.lib.task_adapters.run import database_path, project_outputs, run_procedure
from lab_wizard.lib.workspace import initialize_workspace

DEFINITION: dict[str, Any] = {
    "name": "probe",
    "body": {"type": "wait", "seconds": 0},
    "derived": {"above_dark": 'counts - mean(counts, phase == "background")'},
    "plots": [{"name": "Counts", "x": "bias", "y": ["above_dark"], "where": {"phase": "signal"}}],
}


class Measure(Step):
    def __init__(self, **fields: Any) -> None:
        super().__init__()
        self.fields = fields

    def run(self) -> Status:
        assert self.context is not None
        p = self.context.parameters
        self.context.observe({k: f(p) if callable(f) else f for k, f in self.fields.items()})
        return Status.SUCCESS


def tree() -> Step:
    return Sequence(
        WithParameter("phase", "background", Measure(counts=3.0)),
        WithParameter("phase", "signal", Sweep("bias", [0.01, 0.02, 0.03], lambda b: Measure(
            counts=lambda p: 3.0 + 1000 * p["bias"], voltage=float("nan"), hist=lambda p: [1.0, p["bias"] * 100],
        ))),
    )


@dataclass
class Resources:
    params: Any = None


def _project(tmp_path: Path, outputs: str = "") -> Path:
    """A project on setup ``bench``, which has device A7 mounted."""
    project_dir = tmp_path / "probe_1"
    project_dir.mkdir(parents=True)
    (project_dir / "probe_1.yaml").write_text(
        "project: {measurement_type: probe}\nrun: {operator: andrew}\nsetup: {name: bench}\n"
        + (f"outputs: {outputs}\n" if outputs else ""),
        encoding="utf-8",
    )
    with closing(open_database(database_path(project_dir))) as db:
        with db:
            db.execute("insert or ignore into devices (name) values ('A7')")
        save_setup(db, "bench", {"cryostat": "BF1"}, device="A7")
    return project_dir


def _run(tmp_path: Path, saver: FileSaver | None, **kwargs: Any) -> Path:
    """Run the probe in a project; ``saver`` replaces the ones its outputs: asks for."""
    project_dir = kwargs.pop("project_dir", None) or _project(tmp_path)
    sinks = None if saver is None else [saver]
    status = run_procedure(tree(), Resources(), procedure="probe", definition=DEFINITION, project_dir=project_dir, sinks=sinks)
    assert status is Status.SUCCESS
    return project_dir


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


# --------------------------- a run folder ---------------------------


def test_a_run_is_saved_as_a_folder_named_by_the_template(tmp_path: Path):
    saver = FileSaver()
    project_dir = _run(tmp_path, saver)

    folder = saver.folder.path
    # No workspace, so the project's own data/files, laid out by the default template.
    assert folder.parent.parent == project_dir / "data" / "files"
    assert folder.parent.name == datetime.now().astimezone().date().isoformat()
    assert folder.name.startswith("probe_A7_")
    assert sorted(p.name for p in folder.iterdir()) == [
        "hist.csv", "plot.png", "points.csv", "procedure.yaml", "run.yaml", "steps.csv",
    ]

    run = yaml.safe_load((folder / "run.yaml").read_text())
    assert (run["run_id"], run["procedure"], run["status"], run["device"], run["operator"]) == (
        1, "probe", "success", "A7", "andrew",
    )
    assert (run["setup"], run["setup_fields"]) == ("bench", {"cryostat": "BF1"})
    assert yaml.safe_load((folder / "procedure.yaml").read_text())["plots"][0]["name"] == "Counts"


def test_points_csv_has_one_line_per_point_with_empty_cells_for_what_was_not_recorded(tmp_path: Path):
    saver = FileSaver()
    _run(tmp_path, saver)
    rows = _read_csv(saver.folder.path / "points.csv")

    # The array column has its own file; NaN is an empty cell; steps come last.
    assert rows[0] == ["seq", "t", "phase", "counts", "bias", "voltage", "steps"]
    assert [r[0] for r in rows[1:]] == ["0", "1", "2", "3"]
    assert rows[1][2:6] == ["background", "3.0", "", ""]
    assert rows[2][2:6] == ["signal", "13.0", "0.01", ""]
    assert rows[2][6] == "sequence/with_parameter[1]/sweep[0]/measure#0"

    hist = _read_csv(saver.folder.path / "hist.csv")
    assert hist == [["seq", "0", "1"], ["1", "1.0", "1.0"], ["2", "1.0", "2.0"], ["3", "1.0", "3.0"]]

    steps = _read_csv(saver.folder.path / "steps.csv")
    assert steps[0] == ["path", "kind", "started_at", "ended_at", "status", "error"]
    assert steps[1][:2] == ["sequence", "sequence"] and steps[1][4] == "success"


def test_a_folder_is_a_complete_copy_of_the_runs_database_rows(tmp_path: Path):
    """D15: the same writer backs a live run and an export, so they must match."""
    saver = FileSaver()
    project_dir = _run(tmp_path, saver)
    live = saver.folder.path

    exported = export_run(project_dir / "data" / "lab.db", 1, tmp_path / "export")
    assert exported.relative_to(tmp_path / "export") == live.relative_to(project_dir / "data" / "files")
    for name in ("run.yaml", "procedure.yaml", "points.csv", "steps.csv", "hist.csv"):
        assert (exported / name).read_text() == (live / name).read_text(), name
    assert (exported / "plot.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_folder_template_can_name_any_filter(tmp_path: Path):
    facets = {"date": "2026-09-22", "procedure": "mcr_curve", "device": "A7", "device.wafer": "W12",
              "setup.cryostat": "Blue/Fors 1", "time": "143012"}
    assert folder_name("{device.wafer}/{device}/{date}_{procedure}", facets) == Path("W12/A7/2026-09-22_mcr_curve")
    assert folder_name("{setup.cryostat}/{operator}_{time}", facets) == Path("Blue_Fors 1/none_143012")
    assert folder_name("../{procedure}", facets) == Path("none/mcr_curve")  # cannot climb out of the root


def test_a_template_using_a_device_property_reads_it_from_the_lab_database(tmp_path: Path):
    project_dir = _project(tmp_path)

    with closing(open_database(project_dir / "data" / "lab.db")) as db, db:
        db.execute("""update devices set properties = '{"wafer": "W12"}' where name = 'A7'""")
    saver = FileSaver(path="{device.wafer}/{device}")
    _run(tmp_path, saver, project_dir=project_dir)
    assert saver.folder.path == project_dir / "data" / "files" / "W12" / "A7"

    second = FileSaver(path="{device.wafer}/{device}")
    _run(tmp_path, second, project_dir=project_dir)
    assert second.folder.path.name == "A7_2"  # never overwrites a run


# --------------------------- a project's outputs ---------------------------


def test_a_project_saves_files_by_default_laid_out_by_its_workspace(tmp_path: Path):
    workspace, _ = initialize_workspace(tmp_path / "lab")
    save_data_settings(workspace.config_dir, DataSettings(files=FileSettings(path="{device}/{procedure}")))
    _run(tmp_path, None, project_dir=_project(workspace.projects_dir))

    folder = workspace.data_dir / "files" / "A7" / "probe"
    assert yaml.safe_load((folder / "run.yaml").read_text())["status"] == "success"
    assert (folder / "plot.png").is_file()


def test_a_relative_root_is_relative_to_the_workspace(tmp_path: Path):
    workspace, _ = initialize_workspace(tmp_path / "lab")
    files = FileSettings(root="runs", path="{procedure}", plot_png=False)
    save_data_settings(workspace.config_dir, DataSettings(files=files))
    _run(tmp_path, None, project_dir=_project(workspace.projects_dir))

    folder = workspace.root / "runs" / "probe"
    assert (folder / "points.csv").is_file()
    assert not (folder / "plot.png").exists()


def test_a_project_with_files_off_is_only_recorded_in_the_database(tmp_path: Path):
    project_dir = _run(tmp_path, None, project_dir=_project(tmp_path, outputs="{files: false}"))
    assert find(db=project_dir / "data" / "lab.db").table()["status"].to_list() == ["success"]
    assert not (project_dir / "data" / "files").exists()


def test_a_projects_live_plot_is_the_one_it_names(tmp_path: Path):
    assert project_outputs(_project(tmp_path / "a", outputs="{files: false}")) == []

    _files, window = project_outputs(_project(tmp_path / "b", outputs="{live_plot: window, plot: Counts}"))
    assert isinstance(window, MplPlotter) and window.plot_name == "Counts"

    _files, web = project_outputs(_project(tmp_path / "c", outputs="{live_plot: web}"))
    assert isinstance(web, WebPlotter) and web.plot_name == ""


def test_a_crash_mid_run_leaves_a_readable_folder(tmp_path: Path):
    """No RunEnded: the process died. What was recorded is on disk already."""
    saver = FileSaver(root=str(tmp_path / "files"), path="{procedure}")
    run = RunInfo()
    saver.handle(RunStarted(procedure="probe", columns={"bias": {"unit": "V"}, "counts": {"unit": None}}), run)
    for seq in range(2):
        saver.handle(Point(seq=seq, t=datetime.now().astimezone(), values={"bias": seq * 0.1, "counts": 5}), run)

    folder = tmp_path / "files" / "probe"
    assert yaml.safe_load((folder / "run.yaml").read_text())["status"] == "running"
    rows = _read_csv(folder / "points.csv")
    assert rows[0] == ["seq", "t", "bias", "counts", "steps"]
    assert [[r[0], *r[2:]] for r in rows[1:]] == [["0", "0.0", "5", ""], ["1", "0.1", "5", ""]]


def test_a_saver_that_fails_does_not_fail_the_run(tmp_path: Path, caplog):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("in the way")
    saver = FileSaver(root=str(blocker))
    project_dir = _run(tmp_path, saver)  # asserts SUCCESS

    assert "FileSaver failed and has stopped" in caplog.text
    assert find(db=project_dir / "data" / "lab.db").table()["status"].to_list() == ["success"]


# --------------------------- savers as sinks ---------------------------


def test_a_saver_sees_every_run_message(tmp_path: Path):
    saver = StandInSink()
    run_procedure(tree(), Resources(), procedure="probe", sinks=[saver])
    kinds = {type(m).__name__ for m in saver.messages}
    assert kinds == {"RunStarted", "StepBegan", "StepEnded", "Point", "RunEnded"}
    assert saver.run_started.procedure == "probe"
    assert len(saver.points) == 4
    assert saver.run_ended.status == "success"


# --------------------------- the default plot ---------------------------


def test_the_default_plot_is_the_first_declared_one():
    assert default_plot(DEFINITION, ["phase", "counts", "bias"]).name == "Counts"


def test_without_plots_the_default_is_the_first_recorded_column_against_the_innermost_sweep():
    """One line per value of the sweep around it: nested sweeps never zigzag as one line."""
    definition = {"body": {"type": "sweep", "parameter": "outer", "values": [1], "body": {
        "type": "with_parameter", "parameter": "phase", "value": "x", "body": {
            "type": "sweep", "parameter": "inner", "values": [1], "body": {"type": "count"}}}}}
    spec = default_plot(definition, ["outer", "phase", "inner", "counts", "count_rate"])
    assert (spec.x, spec.y, spec.series) == ("inner", ["counts"], "outer")
    assert default_plot(None, ["a", "b"]) == PlotSpec(x="a", y=["b"], series=None)
    one_sweep = {"body": {"type": "sweep", "parameter": "bias", "values": [1], "body": {"type": "count"}}}
    assert default_plot(one_sweep, ["bias", "counts"]).series is None
    assert default_plot(None, ["a"]) is None
