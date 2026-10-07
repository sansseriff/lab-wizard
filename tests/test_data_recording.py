"""The lab database: its schema, the recorder, facets, and where runs land.

Runs here use the procedure framework directly with small stand-in steps; the
full path (a generated project run as a script, through the workspace's own
instrument server) is in ``test_own_server_projects.py``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from lab_procedure import ProcedureRunner, RunStarted, Sequence, Status, Step, Sweep, Wait, WithParameter
from lab_wizard.lib.data import DatabaseRecorder, DatabaseVersionError, open_database
from lab_wizard.lib.data.facets import run_facets, write_run_facets
from lab_wizard.lib.data.setups import SetupError, save_setup
from lab_wizard.lib.task_adapters.run import database_path, run_procedure
from lab_wizard.lib.workspace import WORKSPACE_ENV, clean_workspace, initialize_workspace


class Record(Step):
    def __init__(self, **fields: Any) -> None:
        super().__init__()
        self.fields = fields

    def run(self) -> Status:
        assert self.context is not None
        self.context.observe(self.fields)
        return Status.SUCCESS


class Boom(Step):
    def run(self) -> Status:
        raise RuntimeError("counter timed out")


_OPEN: list[sqlite3.Connection] = []


@pytest.fixture(autouse=True)
def _close_connections():
    yield
    while _OPEN:
        _OPEN.pop().close()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    _OPEN.append(connection)
    return connection


def _record(tmp_path: Path, tree: Step, started: RunStarted | None = None) -> sqlite3.Connection:
    recorder = DatabaseRecorder(tmp_path / "lab.db")
    runner = ProcedureRunner()
    recorder.attach(runner.context.data_bus, runner.context.status_bus)
    try:
        runner.run(tree, started or RunStarted(procedure="probe"))
    except RuntimeError:
        pass
    finally:
        recorder.close()
    db = _connect(tmp_path / "lab.db")
    db.row_factory = sqlite3.Row
    return db


def _values(db: sqlite3.Connection) -> list[dict[str, Any]]:
    return [json.loads(r["values"]) for r in db.execute('select "values" from points order by seq')]


# --------------------------- the schema ---------------------------


def test_a_new_database_is_stamped_with_its_schema_version(tmp_path: Path):
    open_database(tmp_path / "lab.db").close()
    db = _connect(tmp_path / "lab.db")
    assert db.execute("select value from meta where key = 'schema_version'").fetchone() == ("2",)
    assert {r[0] for r in db.execute("select name from sqlite_master where type = 'table'")} == {
        "meta", "devices", "setups", "runs", "steps", "points", "run_facets", "plot_views",
    }


def test_a_database_from_another_schema_version_is_refused_and_left_alone(tmp_path: Path):
    open_database(tmp_path / "lab.db").close()
    with _connect(tmp_path / "lab.db") as db:
        db.execute("update meta set value = '99' where key = 'schema_version'")
    before = (tmp_path / "lab.db").read_bytes()

    with pytest.raises(DatabaseVersionError, match="schema version 99"):
        open_database(tmp_path / "lab.db")
    assert (tmp_path / "lab.db").read_bytes() == before


def test_a_file_that_is_not_a_lab_database_is_refused(tmp_path: Path):
    with _connect(tmp_path / "measurements.db") as db:
        db.execute("create table measurements (id integer primary key)")
    with pytest.raises(DatabaseVersionError, match="no lab_wizard schema version"):
        open_database(tmp_path / "measurements.db")


# --------------------------- recording a run ---------------------------


def test_a_run_is_recorded_as_rows_steps_and_a_run(tmp_path: Path):
    db = _record(
        tmp_path,
        Sweep("bias_voltage", [0.02, 0.03], lambda b: Sequence(Record(counts=5), Record(device_voltage=0.0))),
        RunStarted(procedure="pcr", device="A7", operator="andrew", params={"gate_s": 1.0},
                   columns={"bias_voltage": {"unit": "V"}, "counts": {"unit": None}}),
    )
    run = db.execute("select * from runs").fetchone()
    assert (run["procedure"], run["status"], run["operator"]) == ("pcr", "success", "andrew")
    assert run["ended_at"] >= run["started_at"]
    assert [r["name"] for r in db.execute("select name from devices")] == ["A7"]
    assert _values(db) == [
        {"bias_voltage": 0.02, "counts": 5, "device_voltage": 0.0},
        {"bias_voltage": 0.03, "counts": 5, "device_voltage": 0.0},
    ]
    # Declared columns keep their units; a recorded one nobody declared is learned.
    assert json.loads(run["columns"]) == {
        "bias_voltage": {"unit": "V"}, "counts": {"unit": None}, "device_voltage": {"unit": None},
    }
    steps = db.execute("select path, kind, status from steps order by id").fetchall()
    assert [tuple(s) for s in steps][:3] == [
        ("sweep", "sweep", "success"),
        ("sweep/sequence#0", "sequence", "success"),
        ("sweep/sequence#0/record[0]", "record", "success"),
    ]
    points = db.execute("select steps from points order by seq").fetchall()
    assert json.loads(points[1]["steps"]) == ["sweep/sequence#1/record[0]", "sweep/sequence#1/record[1]"]


def test_a_failed_run_keeps_its_rows_and_says_which_step_failed(tmp_path: Path):
    db = _record(tmp_path, Sequence(Record(counts=5), Boom()))
    assert db.execute("select status from runs").fetchone()["status"] == "failed"
    assert _values(db) == [{"counts": 5}]
    failed = db.execute("select path, error from steps where status = 'failed' order by id desc").fetchone()
    assert (failed["path"], failed["error"]) == ("sequence/boom[1]", "RuntimeError: counter timed out")


def test_an_aborted_run_is_recorded_as_aborted(tmp_path: Path):
    recorder = DatabaseRecorder(tmp_path / "lab.db")
    runner = ProcedureRunner()
    recorder.attach(runner.context.data_bus, runner.context.status_bus)
    thread = runner.start(Sequence(Record(counts=5), Wait(10.0, progress_interval=0.01)), RunStarted(procedure="p"))
    for _ in range(500):
        if runner.context.latest:
            break
        thread.join(timeout=0.01)
    runner.abort()
    thread.join(timeout=2.0)
    recorder.close()

    db = _connect(tmp_path / "lab.db")
    assert db.execute("select status from runs").fetchone() == ("aborted",)
    assert db.execute('select "values" from points').fetchone() == ('{"counts":5}',)


def test_values_that_are_not_json_are_stored_as_json(tmp_path: Path):
    db = _record(tmp_path, Record(rate=float("nan"), peak=np.float64(2.5), n=np.int64(7), hist=np.arange(3.0)))
    assert _values(db) == [{"rate": None, "peak": 2.5, "n": 7, "hist": [0.0, 1.0, 2.0]}]
    # SQLite can query inside it, which a NaN would have prevented.
    assert db.execute("""select json_extract("values", '$.hist[2]') from points""").fetchone()[0] == 2.0


def test_two_runs_share_one_device_row(tmp_path: Path):
    for _ in range(2):
        recorder = DatabaseRecorder(tmp_path / "lab.db")
        runner = ProcedureRunner()
        recorder.attach(runner.context.data_bus, runner.context.status_bus)
        runner.run(Record(counts=1), RunStarted(procedure="p", device="A7"))
        recorder.close()
    db = _connect(tmp_path / "lab.db")
    assert db.execute("select count(*) from devices").fetchone() == (1,)
    assert db.execute("select count(distinct device_id) from runs").fetchone() == (1,)


# --------------------------- facets ---------------------------


def test_facets_flatten_a_runs_facts_into_filters():
    run = {
        "procedure": "mcr_curve",
        "status": "success",
        "operator": "andrew",
        "project": None,
        "started_at": "2026-09-22T14:30:00+00:00",
        "setup": "cryo-A",
        "setup_fields": {
            "cryostat": "BlueFors1",
            "tags": ["a", "b"],
            "wiring": {"image": "3f9a.jpg"},
            "bias_resistor": {"value": 4.7, "unit": "kΩ"},
        },
        "params": {"readout": {"gate_time_s": 1.0, "enabled": True}},
        "instruments": {"laser": {"class": "Qcl", "type": "daylight_qcl", "params": {"wavelength_um": 4.5}}},
        "columns": {"attenuation_db": {"unit": "dB"}, "counts": {"unit": None}},
    }
    facets = run_facets(run, {"name": "A7", "properties": {"type": "SNSPD-A", "width_nm": 80}})
    as_dict = {(key, value): num for key, value, num in facets}

    assert as_dict[("procedure", "mcr_curve")] is None
    assert ("device", "A7") in as_dict and ("device.type", "SNSPD-A") in as_dict
    assert as_dict[("device.width_nm", "80")] == 80.0
    assert ("setup", "cryo-A") in as_dict and ("setup.cryostat", "BlueFors1") in as_dict
    assert not any(key in ("setup.tags", "setup.wiring") for key, _ in as_dict)  # lists and pictures are not filters
    assert as_dict[("setup.bias_resistor", "4700.0")] == 4700.0  # a quantity, in its base unit
    assert as_dict[("param.readout.gate_time_s", "1.0")] == 1.0
    assert ("param.readout.enabled", "true") in as_dict
    assert ("instrument.laser.type", "daylight_qcl") in as_dict
    assert as_dict[("instrument.laser.wavelength_um", "4.5")] == 4.5
    assert {("column", "attenuation_db"), ("column", "counts")} <= set(as_dict)
    assert not any(key == "project" for key, _ in as_dict)  # nothing to filter by


def test_the_date_facet_is_the_labs_local_date():
    from datetime import datetime

    started = "2026-09-22T23:30:00+00:00"
    expected = datetime.fromisoformat(started).astimezone().date().isoformat()
    assert ("date", expected, None) in run_facets({"started_at": started}, None)


def test_editing_a_device_updates_its_runs_filters(tmp_path: Path):
    db = _record(tmp_path, Record(counts=1), RunStarted(procedure="p", device="A7"))
    db.execute("""update devices set properties = '{"type": "SNSPD-B"}' where name = 'A7'""")
    write_run_facets(db, 1)
    assert ("device.type", "SNSPD-B") in {tuple(r) for r in db.execute("select key, value from run_facets")}


# --------------------------- where runs land ---------------------------


def test_a_project_in_a_workspace_records_to_the_workspaces_data_dir(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path / "ws")
    project_dir = ws.projects_dir / "mcr_curve_1"
    project_dir.mkdir()
    assert database_path(project_dir) == ws.data_dir / "lab.db"


def test_a_project_outside_any_workspace_records_into_its_own_folder(tmp_path: Path, monkeypatch):
    # The environment names a workspace, but a project's own location decides.
    ws, _ = initialize_workspace(tmp_path / "ws")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    project_dir = tmp_path / "elsewhere" / "mcr_curve_1"
    project_dir.mkdir(parents=True)
    assert database_path(project_dir) == project_dir / "data" / "lab.db"


def test_cleaning_a_workspace_keeps_its_recorded_data(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path / "ws")
    (ws.data_dir / "lab.db").write_bytes(b"irreplaceable")
    clean_workspace(ws)
    assert (ws.data_dir / "lab.db").read_bytes() == b"irreplaceable"


@dataclass
class _Resources:
    savers: list = field(default_factory=list)
    plotters: list = field(default_factory=list)
    params: Any = None


def test_a_run_outside_a_project_records_nothing(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run_procedure(Record(counts=1), _Resources(), procedure="p") is Status.SUCCESS
    assert list(tmp_path.iterdir()) == []


def _project_on_setup(tmp_path: Path, setup_block: str) -> Path:
    project_dir = tmp_path / "probe_1"
    project_dir.mkdir()
    (project_dir / "probe_1.yaml").write_text(
        "project: {measurement_type: probe}\n"
        "run: {operator: andrew, notes: first cooldown}\n"
        f"setup: {setup_block}\n",
        encoding="utf-8",
    )
    lab = open_database(project_dir / "data" / "lab.db")
    lab.execute("insert into devices (name) values ('A7')")
    lab.commit()
    save_setup(lab, "cryo-A", {"cryostat": "BlueFors1", "bias_resistor": {"value": 100, "unit": "kΩ"}}, device="A7")
    lab.close()
    return project_dir


def test_a_project_run_records_its_run_block_and_a_copy_of_its_setup(tmp_path: Path):
    project_dir = _project_on_setup(tmp_path, "{name: cryo-A}")
    tree = WithParameter("phase", "signal", Record(counts=1))
    assert run_procedure(tree, _Resources(), procedure="probe", project_dir=project_dir) is Status.SUCCESS

    db = _connect(project_dir / "data" / "lab.db")
    db.row_factory = sqlite3.Row
    run = db.execute("select r.*, d.name as device from runs r join devices d on d.id = r.device_id").fetchone()
    assert (run["operator"], run["notes"], run["project"]) == ("andrew", "first cooldown", "probe_1")
    # The device is the one mounted in the setup; the fields are copied as they were.
    assert (run["device"], run["setup"]) == ("A7", "cryo-A")
    assert json.loads(run["setup_fields"])["cryostat"] == "BlueFors1"
    assert run["definition"] is None  # a step tree with no definition behind it

    # Changing the setup later changes no run.
    lab = open_database(project_dir / "data" / "lab.db")
    save_setup(lab, "cryo-A", {"cryostat": "BlueFors2"}, device="A7")
    assert json.loads(lab.execute("select setup_fields from runs").fetchone()[0])["cryostat"] == "BlueFors1"
    lab.close()


NEEDS_DEFINITION = {"name": "probe", "needs": {"bias_resistance": {"unit": "ohm"}}}


def test_a_run_whose_needs_its_setup_cannot_fill_never_starts(tmp_path: Path):
    project_dir = _project_on_setup(tmp_path, "{name: cryo-A, needs: {bias_resistance: cryostat}}")
    with pytest.raises(SetupError, match="cryostat is not a number"):
        run_procedure(Record(counts=1), _Resources(), procedure="probe", definition=NEEDS_DEFINITION,
                      project_dir=project_dir)
    db = _connect(project_dir / "data" / "lab.db")
    assert db.execute("select count(*) from runs").fetchone()[0] == 0


def test_a_run_records_which_field_filled_each_need(tmp_path: Path):
    project_dir = _project_on_setup(tmp_path, "{name: cryo-A, needs: {bias_resistance: bias_resistor}}")
    assert run_procedure(Record(counts=1), _Resources(), procedure="probe", definition=NEEDS_DEFINITION,
                         project_dir=project_dir) is Status.SUCCESS
    db = _connect(project_dir / "data" / "lab.db")
    (needs,) = db.execute("select setup_needs from runs").fetchone()
    assert json.loads(needs) == {"bias_resistance": "bias_resistor"}


# --------------------------------------------------------------------------
# A run whose process dies without ending it
# --------------------------------------------------------------------------

_RECORD_THEN_DIE = """
import os, sys
from lab_procedure import MessageBus, Point, RunStarted, StepBegan
from lab_procedure.messages import now
from lab_wizard.lib.data import DatabaseRecorder

recorder = DatabaseRecorder(sys.argv[1])
data, status = MessageBus(), MessageBus()
recorder.attach(data, status)
data.emit(RunStarted(procedure="probe"))
status.emit(StepBegan(("sweep",), None, "sweep", True, kind="sweep"))
data.emit(Point(seq=0, t=now(), values={"bias": 0.1, "counts": 7}))
print("recorded", flush=True)
sys.stdin.read()          # the test kills this process here
"""


def test_a_run_whose_process_is_killed_ends_interrupted(tmp_path: Path) -> None:
    import signal
    import subprocess
    import sys

    from lab_wizard.lib.data import find

    db = tmp_path / "lab.db"
    process = subprocess.Popen(
        [sys.executable, "-c", _RECORD_THEN_DIE, str(db)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None and process.stdout.readline().strip() == "recorded"
        # Alive: still running, whoever looks.
        (run,) = find(db=db).table().to_dicts()
        assert run["status"] == "running"

        process.send_signal(signal.SIGKILL)
        process.wait(10)
    finally:
        process.kill()

    runs = find(db=db)
    (run,) = runs.table().to_dicts()
    assert run["status"] == "interrupted"
    assert run["ended_at"] is not None
    # Its columns survive, and it is found by filters like any ended run.
    assert list(runs.columns()) == ["bias", "counts"]
    assert len(find({"status": "interrupted"}, db=db)) == 1
    (step,) = runs.steps().to_dicts()
    assert step["status"] == "interrupted" and step["ended_at"] is not None


def test_a_run_that_ends_normally_is_not_marked_interrupted(tmp_path: Path) -> None:
    from lab_wizard.lib.data import find

    _record(tmp_path, Record(counts=3))
    (run,) = find(db=tmp_path / "lab.db").table().to_dicts()
    assert run["status"] == "success"
    assert not list((tmp_path / ".running").glob("*.lock"))


def test_a_database_on_a_network_share_is_warned_about(tmp_path: Path, monkeypatch, caplog):
    from lab_wizard.lib.data import network

    share = (tmp_path / "share").resolve()
    share.mkdir()
    monkeypatch.setattr(network.sys, "platform", "darwin")
    monkeypatch.setattr(network, "_mounts", lambda: [("/", "apfs"), (str(share), "smbfs")])
    assert network.network_filesystem(share / "data" / "lab.db") == "smbfs"  # not created yet
    assert network.network_filesystem(tmp_path / "local.db") is None

    DatabaseRecorder(share / "lab.db").close()
    assert "network share (smbfs)" in caplog.text
