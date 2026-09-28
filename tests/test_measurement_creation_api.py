"""Measurement creation, rewired (procedure plan Phase 5), through the API.

Procedures appear beside hand-written measurements, take presets, and generate
through the same endpoint; removing an instrument lists the projects that use
it; and this workspace's own server is a source an instrument can be picked
from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from lab_wizard.lib.procedures.storage import load_procedure, save_preset
from lab_wizard.lib.utilities.config_io import load_instruments
from lab_wizard.wizard.backend.main import app
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rig):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    rig.write(ws.config_dir)
    # Not entered as a context manager: that runs the app's startup, which
    # configures logging and tries to start a server for the workspace — both
    # of which leak into every test that runs after this file. The app caches
    # its workspace on app.state, so it is cleared for each test instead.
    monkeypatch.setattr(app.state, "env", None, raising=False)
    yield ws, TestClient(app)


def _mcr_selections(rig) -> list[dict[str, Any]]:
    selections = [
        rig.select("voltage_source", "source"),
        rig.select("voltage_sense", "meter"),
        rig.select("counter", "counter"),
        rig.select("attenuator", "attenuator"),
    ]
    return [selection.model_dump(mode="json") for selection in selections]


def test_procedures_are_offered_with_their_presets(workspace):
    ws, client = workspace
    definition = load_procedure(ws.config_dir, "mcr_curve")
    save_preset(ws.config_dir, "mcr_curve", "bench", {"bias": {"voltage": 0.02}}, definition.params_model())

    choices = {(c["name"], c["kind"]): c for c in client.get("/api/measurement-choices").json()["choices"]}
    assert ("iv_curve", "procedure") in choices
    mcr = choices[("mcr_curve", "procedure")]
    assert mcr["origin"] == "builtin"
    assert mcr["presets"] == ["bench"]
    assert mcr["roles"]["attenuator"] == "Attenuator"


def test_a_procedures_roles_are_its_requirements(workspace):
    _ws, client = workspace
    reqs = {r["variable_name"]: r for r in client.get("/api/get-resources/mcr_curve?kind=procedure").json()}
    assert set(reqs) == {"voltage_source", "voltage_sense", "counter", "attenuator"}
    matched = {m["class_name"] for m in reqs["attenuator"]["matching_instruments"]}
    assert "YokoAttenuator" in matched
    assert client.get("/api/get-resources/nothing?kind=procedure").status_code == 404


def test_a_project_is_created_from_a_procedure_with_a_preset(workspace, rig):
    ws, client = workspace
    definition = load_procedure(ws.config_dir, "mcr_curve")
    save_preset(ws.config_dir, "mcr_curve", "bench", {"bias": {"voltage": 0.02}}, definition.params_model())

    response = client.post(
        "/api/create-measurement-project",
        json={
            "measurement_name": "mcr_curve",
            "kind": "procedure",
            "params_preset": "bench",
            "selected_resources": _mcr_selections(rig),
        },
    )
    assert response.status_code == 200, response.text
    payload = YAML(typ="safe").load(Path(response.json()["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["measurement"]["params"]["bias"]["voltage"] == 0.02
    assert "instruments" not in payload["resources"]
    assert len(payload["resources"]["instrument_sources"]) == 4
    # Files are saved by default; no live plot unless asked for.
    assert payload["outputs"] == {"files": True, "live_plot": "none", "plot": ""}


def test_a_projects_outputs_are_what_was_chosen(workspace, rig):
    _ws, client = workspace
    response = client.post(
        "/api/create-measurement-project",
        json={
            "measurement_name": "mcr_curve",
            "kind": "procedure",
            "selected_resources": _mcr_selections(rig),
            "outputs": {"files": False, "live_plot": "web"},
        },
    )
    assert response.status_code == 200, response.text
    payload = YAML(typ="safe").load(Path(response.json()["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["outputs"] == {"files": False, "live_plot": "web", "plot": ""}
    listed = client.get("/api/projects").json()["projects"][0]
    assert listed["outputs"] == {"files": False, "live_plot": "web", "plot": ""}
    setup = Path(response.json()["setup_file"]).read_text(encoding="utf-8")
    assert "saver" not in setup.lower() and "plotter" not in setup.lower()


def test_an_unknown_style_is_refused(workspace, rig):
    _ws, client = workspace
    response = client.post(
        "/api/create-measurement-project",
        json={
            "measurement_name": "mcr_curve",
            "kind": "procedure",
            "generation_style": "pedagogical_yaml_expanded",
            "selected_resources": _mcr_selections(rig),
        },
    )
    assert response.status_code == 422
    assert "generation_style" in response.text


def test_removing_an_instrument_lists_the_projects_that_use_it(workspace, rig):
    ws, client = workspace
    created = client.post(
        "/api/create-measurement-project",
        json={"measurement_name": "mcr_curve", "kind": "procedure", "selected_resources": _mcr_selections(rig)},
    ).json()
    counter_name = load_instruments(ws.config_dir)[rig.counter].channels[0].attribute_name

    impact = client.post(
        "/api/manage-instruments/removal-impact", json={"type": "keysight53220A", "key": rig.counter}
    ).json()
    assert [p["name"] for p in impact["projects"]] == [created["project_name"]]
    assert impact["projects"][0]["attributes"] == [counter_name]

    # An instrument inside a rack is found too, not only a top-level one.
    meter = client.post(
        "/api/manage-instruments/removal-impact", json={"type": "sim970", "key": rig.meter}
    ).json()
    assert [p["name"] for p in meter["projects"]] == [created["project_name"]]
    projects = client.get("/api/projects").json()["projects"]
    assert counter_name in projects[0]["instruments"]


def test_this_workspaces_own_server_is_a_source_to_pick_from(tmp_path: Path, monkeypatch):
    from lab_wizard.lib.client.server_registry import advertise_server
    from lab_wizard.wizard.backend import instrument_sources as sources_mod

    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "registry"))
    config_dir = tmp_path / "ws" / "config"
    (config_dir / "instruments").mkdir(parents=True)
    advertise_server(config_dir, bind=None, ipc="ipc:///tmp/lw-own.sock")

    def unreachable(entry, name, url):
        return {"name": name, "kind": "machine", "label": name, "url": url, "tree": [], "reachable": False}

    monkeypatch.setattr(sources_mod, "_machine_source", unreachable)
    listing = sources_mod.list_instrument_sources(str(config_dir))
    own = [s for s in listing["sources"] if s.get("is_own_server")]
    assert len(own) == 1
    assert own[0]["label"] == "This workspace, through its server"
    assert listing["own_server"]["name"] == own[0]["name"]
