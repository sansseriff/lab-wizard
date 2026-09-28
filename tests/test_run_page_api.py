"""The Run page's API: a project's settings, and running it from the wizard.

A project is created through the API onto the simulated bench, its settings
are edited as the page edits them, and it is launched as its own process —
the same setup file a person would run — then followed to its recorded run,
or stopped.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lab_wizard.lib.data import find
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace
from lab_wizard.wizard.backend.main import app


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rig):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    rig.write(ws.config_dir, ("source", "meter"))
    monkeypatch.setattr(app.state, "env", None, raising=False)  # see test_measurement_creation_api
    yield ws, TestClient(app)


def _create(client: TestClient, rig, name: str = "bias_sweep") -> str:
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
    return response.json()["project_name"]


def _wait_until_ended(client: TestClient, project: str, timeout: float = 60) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/projects/{project}/launch").json()
        if status["state"] == "ended":
            return status
        time.sleep(0.2)
    raise AssertionError(f"the run never ended: {status}")


# --------------------------- settings ---------------------------


def test_a_projects_settings_are_its_run_params_and_outputs(workspace, rig):
    _ws, client = workspace
    project = _create(client, rig)
    settings = client.get(f"/api/projects/{project}/settings").json()
    assert (settings["measurement"], settings["kind"]) == ("bias_sweep", "custom")
    assert settings["run"] == {"device": None, "operator": None, "notes": None, "metadata": {}}
    assert settings["params"]["settle_s"] == 0.05
    assert settings["outputs"]["files"] is False
    assert settings["params_schema"]["properties"]["settle_s"]["type"] == "number"
    assert "resources:" in settings["yaml"]
    assert client.get("/api/projects/nothing/settings").status_code == 404


def test_saving_sections_keeps_the_rest_of_the_file(workspace, rig):
    _ws, client = workspace
    project = _create(client, rig)
    run = {"device": "A7", "operator": "andrew", "notes": "after rewiring",
           "metadata": {"cryostat": "BF1", "optics": {"fiber": "SM28", "attenuation_db": 30}}}
    saved = client.put(f"/api/projects/{project}/settings", json={"run": run}).json()
    assert saved["run"] == run
    assert "instrument_sources" in saved["yaml"]  # the resources were not touched


def test_a_bad_setting_is_refused_with_where_it_is(workspace, rig):
    _ws, client = workspace
    project = _create(client, rig)
    before = client.get(f"/api/projects/{project}/settings").json()["yaml"]

    response = client.put(f"/api/projects/{project}/settings", json={"params": {"settle_s": "soon"}})
    assert response.status_code == 422
    (problem,) = response.json()["detail"]["problems"]
    assert problem["path"] == ["measurement", "params", "settle_s"]

    response = client.put(f"/api/projects/{project}/settings", json={"outputs": {"live_plot": "hologram"}})
    assert response.json()["detail"]["problems"][0]["path"] == ["outputs", "live_plot"]

    response = client.put(f"/api/projects/{project}/settings", json={"yaml": "run: [unclosed"})
    assert "not valid YAML" in response.json()["detail"]["message"]
    assert client.get(f"/api/projects/{project}/settings").json()["yaml"] == before  # nothing written


# --------------------------- running ---------------------------


def test_a_launched_run_is_followed_to_its_record(workspace, rig):
    ws, client = workspace
    project = _create(client, rig)
    client.put(f"/api/projects/{project}/settings", json={
        "params": {"bias": {"mode": "explicit", "values": [0.0, 0.01, 0.02]}, "settle_s": 0.0},
        "run": {"device": "A7", "metadata": {"cryostat": "BF1", "optics": {"fiber": "SM28"}}},
    })
    assert client.get(f"/api/projects/{project}/launch").json() == {"state": "idle"}

    started = client.post(f"/api/projects/{project}/launch").json()
    assert started["state"] in ("starting", "running", "ended")
    status = _wait_until_ended(client, project)
    assert status["exit_code"] == 0, status["log"]
    assert status["run_id"] == 1

    runs = find(db=ws.data_dir / "lab.db", procedure="bias_sweep")
    assert runs.table()["status"].to_list() == ["success"]
    # The metadata's groups are filters on the Data page.
    facets = {f["key"] for f in client.get("/api/data/facets").json()["facets"]}
    assert {"run.cryostat", "run.optics.fiber", "device"} <= facets


def test_a_run_is_stopped_like_a_ctrl_c(workspace, rig):
    ws, client = workspace
    project = _create(client, rig)
    client.put(f"/api/projects/{project}/settings", json={
        "params": {"bias": {"mode": "linear", "start": 0.0, "stop": 0.5, "step": 0.005}, "settle_s": 0.2},
    })
    client.post(f"/api/projects/{project}/launch")
    deadline = time.monotonic() + 30
    while client.get(f"/api/projects/{project}/launch").json()["state"] != "running":
        assert time.monotonic() < deadline, client.get(f"/api/projects/{project}/launch").json()
        time.sleep(0.1)
    assert client.post(f"/api/projects/{project}/launch").status_code == 409  # one at a time

    client.post(f"/api/projects/{project}/stop")
    status = _wait_until_ended(client, project)
    assert status["exit_code"] != 0
    assert find(db=ws.data_dir / "lab.db").table()["status"].to_list() == ["aborted"]
    assert client.post(f"/api/projects/{project}/stop").status_code == 409


def test_params_are_written_with_their_descriptions_as_comments(workspace, rig):
    """As instrument configs are: the YAML says what each param means."""
    _ws, client = workspace
    project = _create(client, rig)
    comment = "# wait after setting each bias before reading, in seconds"
    assert comment in client.get(f"/api/projects/{project}/settings").json()["yaml"]
    saved = client.put(f"/api/projects/{project}/settings", json={"params": {"settle_s": 0.2}}).json()
    assert "settle_s: 0.2" in saved["yaml"] and comment in saved["yaml"]
