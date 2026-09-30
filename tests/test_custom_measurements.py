"""Custom measurements: Python files in a workspace's measurements folder.

A new workspace gets the examples; the wizard offers every file there beside
the procedures; and a project generated from one runs as a script onto the
simulated bench, recorded like any other run, with the plots it declared.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from lab_wizard.lib.custom_measurements import list_custom_measurements
from lab_wizard.lib.data import find
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace
from lab_wizard.wizard.backend.main import app

EXAMPLES = {"bias_sweep", "find_switching_voltage"}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rig):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    rig.write(ws.config_dir, ("source", "meter"))
    monkeypatch.setattr(app.state, "env", None, raising=False)  # see test_measurement_creation_api
    yield ws, TestClient(app)


# --------------------------- the folder ---------------------------


def test_a_new_workspace_has_the_example_measurements(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path)
    assert {p.stem for p in ws.measurements_dir.glob("*.py")} == EXAMPLES

    # The folder is the lab's own from then on: a deleted example stays deleted.
    (ws.measurements_dir / "bias_sweep.py").unlink()
    initialize_workspace(tmp_path)
    assert {p.stem for p in ws.measurements_dir.glob("*.py")} == {"find_switching_voltage"}


def test_the_examples_declare_their_roles_params_and_plots(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path)
    found = list_custom_measurements(ws.measurements_dir)
    assert set(found) == EXAMPLES
    for name in EXAMPLES:
        measurement = found[name]
        assert not isinstance(measurement, str), measurement
        assert measurement.roles == {"voltage_source": (VSource, False), "voltage_sense": (VSense, False)}
        assert measurement.description
        assert measurement.plots
        measurement.params_model()  # every setting has a default


def test_a_broken_file_is_listed_with_why(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path)
    (ws.measurements_dir / "half_written.py").write_text("class Params: pass\n", encoding="utf-8")
    (ws.measurements_dir / "_helpers.py").write_text("X = 1\n", encoding="utf-8")
    found = list_custom_measurements(ws.measurements_dir)
    assert "Resources" in found["half_written"] and "build_procedure" in found["half_written"]
    assert "_helpers" not in found  # a leading underscore is a helper, not a measurement


# --------------------------- through the wizard ---------------------------


def test_custom_measurements_are_offered_beside_procedures(workspace):
    _ws, client = workspace
    choices = {(c["name"], c["kind"]): c for c in client.get("/api/measurement-choices").json()["choices"]}
    sweep = choices[("bias_sweep", "custom")]
    assert sweep["origin"] == "workspace"
    assert sweep["roles"] == {"voltage_source": "VSource", "voltage_sense": "VSense"}
    assert ("iv_curve", "procedure") in choices

    reqs = client.get("/api/get-resources/find_switching_voltage?kind=custom").json()
    assert {r["variable_name"] for r in reqs} == {"voltage_source", "voltage_sense"}
    assert client.get("/api/get-resources/nothing?kind=custom").status_code == 404


def _create(client: TestClient, rig, name: str) -> dict[str, Any]:
    selections = [rig.select("voltage_source", "source"), rig.select("voltage_sense", "meter")]
    response = client.post(
        "/api/create-measurement-project",
        json={
            "measurement_name": name,
            "kind": "custom",
            "selected_resources": [s.model_dump(mode="json") for s in selections],
            "outputs": {"files": False},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _set_params(yaml_file: Path, params: dict[str, Any]) -> None:
    yaml = YAML(typ="rt")
    payload = yaml.load(yaml_file.read_text(encoding="utf-8"))
    payload["measurement"]["params"].update(params)
    with yaml_file.open("w", encoding="utf-8") as handle:
        yaml.dump(payload, handle)


def _run_script(out: dict[str, Any]) -> None:
    setup = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup)], capture_output=True, text=True, timeout=300, cwd=str(setup.parent)
    )
    assert result.returncode == 0, result.stderr


def test_the_switching_search_stops_where_the_detector_switched(workspace, rig):
    """The example that decides as it goes, run as a script onto the simulated detector."""
    ws, client = workspace
    out = _create(client, rig, "find_switching_voltage")
    assert Path(out["measurement_file"]).read_text() == (ws.measurements_dir / "find_switching_voltage.py").read_text()
    # The project starts from the file's own Params defaults: adding a
    # measurement never needs a change in the generator.
    params = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))["measurement"]["params"]
    assert params == {"start_v": 0.0, "stop_v": 2.0, "step_v": 0.01, "threshold_v": 0.01, "settle_s": 0.02}
    _set_params(Path(out["yaml_file"]), {"start_v": 0.0, "stop_v": 0.2, "step_v": 0.005, "settle_s": 0.0})
    _run_script(out)

    runs = find(db=ws.data_dir / "lab.db", procedure="find_switching_voltage")
    assert runs.table()["status"].to_list() == ["success"]
    rows = runs.points().sort("seq")
    switched = rows["switched"].to_list()
    assert switched[-1] is True and not any(switched[:-1]), "stops at the first switched reading"
    assert rows["bias_voltage"][-1] < 0.2, "the detector switched before the end of the ramp"

    # The plots it declared are what the Data page draws for the run.
    detail = client.get(f"/api/data/runs/{runs.ids[0]}").json()
    assert [p["name"] for p in detail["plots"]] == ["Ramp"]


def test_the_bias_sweep_example_runs_every_bias(workspace, rig):
    ws, client = workspace
    out = _create(client, rig, "bias_sweep")
    _set_params(
        Path(out["yaml_file"]),
        {"bias": {"mode": "explicit", "values": [0.0, 0.01, 0.02]}, "settle_s": 0.0},
    )
    _run_script(out)
    rows = find(db=ws.data_dir / "lab.db", procedure="bias_sweep").points()
    assert rows["bias_voltage"].to_list() == [0.0, 0.01, 0.02]


def test_a_measurement_named_like_a_standard_module_does_not_shadow_it(workspace, rig):
    """It is loaded by path, in the wizard and in its project, never imported by its bare name."""
    ws, client = workspace
    source = (ws.measurements_dir / "bias_sweep.py").read_text(encoding="utf-8")
    (ws.measurements_dir / "queue.py").write_text(source, encoding="utf-8")
    out = _create(client, rig, "queue")
    setup = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "load_module(" in setup and not (Path(out["project_dir"]) / "queue.py").exists()
    _set_params(Path(out["yaml_file"]), {"bias": {"mode": "explicit", "values": [0.0, 0.01]}, "settle_s": 0.0})
    _run_script(out)  # the setup's own imports (logging, threading) use the real queue module
    runs = find(db=ws.data_dir / "lab.db", procedure="queue")
    assert runs.table()["status"].to_list() == ["success"]


def test_a_file_name_that_is_not_a_python_name_is_listed_with_how_to_fix_it(tmp_path: Path):
    ws, _ = initialize_workspace(tmp_path)
    (ws.measurements_dir / "bias-sweep.py").write_text("x = 1\n", encoding="utf-8")
    found = list_custom_measurements(ws.measurements_dir)
    assert "rename it, e.g. bias_sweep.py" in found["bias-sweep"]
