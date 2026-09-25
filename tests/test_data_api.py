"""The Data page's API: filters, runs, plots and devices, over a recorded workspace.

Runs are recorded the way a project records them, into the workspace's lab
database, and read back through the HTTP routes the page calls. Two are named
after the built-in ``mcr_curve``, recorded with a bare definition, so what they
are drawn with has to come from the procedure as it is now; a third belongs to
a procedure that no longer exists, so it falls back to what it recorded.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from lab_procedure import Sequence, Status, Step, Sweep, WithParameter
from lab_wizard.lib.task_adapters.run import run_procedure
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace
from lab_wizard.wizard.backend.main import app

ATTENUATIONS = [10.0, 0.0]


class Measure(Step):
    def __init__(self, **fields: Any) -> None:
        super().__init__()
        self.fields = fields

    def run(self) -> Status:
        assert self.context is not None
        p = self.context.parameters
        self.context.observe({k: f(p) if callable(f) else f for k, f in self.fields.items()})
        return Status.SUCCESS


def mcr_tree(brightness: float) -> Step:
    """A background count, then the count rate at each attenuation."""
    return Sequence(
        WithParameter("phase", "background", Measure(count_rate=10.0)),
        WithParameter("phase", "signal", Sweep("attenuation_db", ATTENUATIONS, lambda a: Measure(
            count_rate=lambda p: 10.0 + brightness * 10 ** (-p["attenuation_db"] / 10),
            attenuation_db_reached=lambda p: p["attenuation_db"],
            device_voltage=0.0,
        ))),
    )


class Params(BaseModel):
    gate_time_s: float


@dataclass
class Resources:
    params: Any = None
    savers: list = field(default_factory=list)
    plotters: list = field(default_factory=list)


def _record(projects: Path, name: str, device: str, procedure: str, tree: Step, definition: dict, gate: float) -> None:
    project_dir = projects / name
    project_dir.mkdir(parents=True)
    (project_dir / f"{name}.yaml").write_text(
        f"project: {{measurement_type: {procedure}}}\n"
        f"run: {{device: {device}, operator: andrew, metadata: {{cryostat: BF1}}}}\n",
        encoding="utf-8",
    )
    status = run_procedure(
        tree, Resources(params=Params(gate_time_s=gate)), procedure=procedure,
        definition=definition, project_dir=project_dir,
    )
    assert status is Status.SUCCESS


PROBE_DEFINITION = {
    "name": "probe",
    "body": {"type": "wait", "seconds": 0},
    "derived": {"double": "counts * 2"},
    "plots": [{"name": "Doubled", "x": "bias", "y": ["double"]}],
}


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    # See test_measurement_creation_api: no lifespan, and no cached workspace.
    monkeypatch.setattr(app.state, "env", None, raising=False)
    return ws


@pytest.fixture
def client(workspace):
    """Three runs: mcr_curve on A7 and B2, then a probe run, in that order."""
    bare = {"name": "mcr_curve", "body": {"type": "wait", "seconds": 0}}
    _record(workspace.projects_dir, "mcr_a", "A7", "mcr_curve", mcr_tree(1000.0), bare, gate=0.1)
    _record(workspace.projects_dir, "mcr_b", "B2", "mcr_curve", mcr_tree(2000.0), bare, gate=0.2)
    probe = Sweep("bias", [0.1, 0.2], lambda b: Measure(counts=lambda p: p["bias"] * 10))
    _record(workspace.projects_dir, "probe", "A7", "probe", probe, PROBE_DEFINITION, gate=0.1)
    return TestClient(app)


def _filters(**filters: Any) -> dict[str, str]:
    return {"filters": json.dumps(filters)}


def _facet(body: dict, key: str) -> dict:
    return next(f for f in body["facets"] if f["key"] == key)


# --------------------------- the sidebar ---------------------------


def test_the_sidebar_lists_every_filter_with_its_run_counts(client):
    body = client.get("/api/data/facets").json()
    assert body["runs"] == 3
    procedure = _facet(body, "procedure")
    assert procedure["group"] == "Procedure"
    assert {v["value"]: v["runs"] for v in procedure["values"]} == {"mcr_curve": 2, "probe": 1}
    # Sections come in the sidebar's order.
    groups = list(dict.fromkeys(f["group"] for f in body["facets"]))
    assert groups[:2] == ["Procedure", "Device"]
    assert _facet(body, "run.cryostat")["group"] == "Run"


def test_a_chosen_filter_still_shows_its_alternatives(client):
    body = client.get("/api/data/facets", params=_filters(procedure="mcr_curve")).json()
    assert body["runs"] == 2
    assert {v["value"]: v["runs"] for v in _facet(body, "procedure")["values"]} == {"mcr_curve": 2, "probe": 1}
    assert {v["value"]: v["runs"] for v in _facet(body, "device")["values"]} == {"A7": 1, "B2": 1}


def test_a_numeric_filter_offers_its_range(client):
    gate = _facet(client.get("/api/data/facets").json(), "param.gate_time_s")
    assert gate["numeric"] is True
    assert gate["range"] == [0.1, 0.2]
    assert _facet(client.get("/api/data/facets").json(), "procedure")["numeric"] is False

    runs = client.get("/api/data/runs", params=_filters(**{"param.gate_time_s": {"range": [0.15, 1]}})).json()
    assert [r["project"] for r in runs["runs"]] == ["mcr_b"]


def test_a_malformed_filter_is_refused_with_the_reason(client):
    assert client.get("/api/data/facets", params={"filters": "{not json"}).status_code == 422
    response = client.get("/api/data/runs", params=_filters(procedure={"between": [1, 2]}))
    assert response.status_code == 422
    assert "range" in response.json()["detail"]


def test_before_the_first_run_everything_is_empty_not_an_error(workspace):
    client = TestClient(app)
    assert client.get("/api/data/facets").json() == {"runs": 0, "facets": []}
    assert client.get("/api/data/runs").json()["runs"] == []
    assert client.get("/api/data/devices").json() == {"devices": []}
    assert client.get("/api/data/runs/1").status_code == 404


# --------------------------- the run list and one run ---------------------------


def test_runs_come_newest_first_a_page_at_a_time(client):
    body = client.get("/api/data/runs").json()
    assert body["total"] == 3
    assert [(r["procedure"], r["device"]) for r in body["runs"]] == [("probe", "A7"), ("mcr_curve", "B2"), ("mcr_curve", "A7")]
    assert body["runs"][0]["points"] == 2

    second = client.get("/api/data/runs", params={"page": 2, "page_size": 2}).json()
    assert [r["device"] for r in second["runs"]] == ["A7"]
    assert second["total"] == 3


def _ids(client) -> dict[str, int]:
    return {r["project"]: r["id"] for r in client.get("/api/data/runs").json()["runs"]}


def test_a_run_is_shown_with_its_procedures_current_plots_and_derived_columns(client):
    """Recorded with a bare definition; drawn by mcr_curve as it is now."""
    detail = client.get(f"/api/data/runs/{_ids(client)['mcr_a']}").json()
    assert detail["definition_source"] == "procedure"
    assert [p["name"] for p in detail["plots"]] == ["MCR", "Attenuation reached", "Device voltage"]
    assert all(p["runs"] == [_ids(client)["mcr_a"]] for p in detail["plots"])
    assert "rate_above_dark" in detail["derived"]
    assert detail["run"]["device"] == "A7"
    assert detail["run"]["metadata"] == {"cryostat": "BF1"}
    assert detail["params"] == {"gate_time_s": 0.1}
    assert set(detail["columns"]) >= {"phase", "count_rate", "attenuation_db"}


def test_a_run_whose_procedure_is_gone_is_drawn_as_it_recorded(client):
    detail = client.get(f"/api/data/runs/{_ids(client)['probe']}").json()
    assert detail["definition_source"] == "recorded"
    assert [p["name"] for p in detail["plots"]] == ["Doubled"]
    assert detail["derived"] == {"double": "counts * 2"}


def test_an_unknown_run_is_not_found(client):
    assert client.get("/api/data/runs/999").status_code == 404
    assert client.get("/api/data/runs/999/steps").status_code == 404


def test_a_runs_timeline_and_a_points_steps(client):
    run_id = _ids(client)["mcr_a"]
    steps = client.get(f"/api/data/runs/{run_id}/steps").json()["steps"]
    assert steps[0]["path"] == "sequence" and steps[0]["status"] == "success"
    assert {"path", "kind", "started_at", "ended_at", "status", "error"} == set(steps[0])

    point = client.get(f"/api/data/runs/{run_id}/points/1").json()
    assert point["values"]["attenuation_db"] == 10.0
    assert point["steps"][-1].endswith("measure#0")
    assert client.get(f"/api/data/runs/{run_id}/points/99").status_code == 404


# --------------------------- plots ---------------------------


def _mcr_spec(client, **overrides: Any) -> dict[str, Any]:
    spec = client.get(f"/api/data/runs/{_ids(client)['mcr_a']}").json()["plots"][0]
    return {**spec, **overrides}


def test_two_runs_overlay_with_their_backgrounds_subtracted(client):
    ids = _ids(client)
    spec = _mcr_spec(client, runs=[ids["mcr_a"], ids["mcr_b"]], label="device")
    body = client.post("/api/data/plot", json={"spec": spec}).json()

    assert [s["label"] for s in body["series"]] == ["A7", "B2"]
    a7, b2 = body["series"]
    assert a7["x"] == ATTENUATIONS
    assert a7["y"] == pytest.approx([100.0, 1000.0])  # 10 + 1000·T, minus the 10 of background
    assert b2["y"] == pytest.approx([200.0, 2000.0])
    # Which point each value is, so a click can find its steps.
    assert a7["run_id"] == [ids["mcr_a"]] * 2
    assert a7["seq"] == [1, 2]
    assert body["units"]["attenuation_db"] is None or isinstance(body["units"]["attenuation_db"], str)


def test_a_plot_the_page_cannot_draw_says_why(client):
    bad = client.post("/api/data/plot", json={"spec": _mcr_spec(client, y=["count_rate +"])})
    assert bad.status_code == 422
    unknown = client.post("/api/data/plot", json={"spec": _mcr_spec(client, y=["no_such_column"])})
    assert unknown.status_code == 422
    empty = client.post("/api/data/plot", json={"spec": _mcr_spec(client, runs=[])})
    assert empty.status_code == 422 and "choose" in empty.json()["detail"]


def test_the_notebook_carries_the_derived_columns_it_needs(client):
    source = client.post("/api/data/plot/notebook", json={"spec": _mcr_spec(client)}).json()["source"]
    compile(source, "notebook", "exec")
    assert "rate_above_dark" in source
    assert "mean(count_rate, phase == \"background\")" in source.replace("'", '"')
    assert "load_plot(spec" in source


def test_a_plot_built_on_the_page_is_saved_into_its_procedure(client, workspace):
    counts = {"name": "Raw counts", "x": "attenuation_db", "y": ["count_rate"], "runs": [1, 2]}
    saved = client.post("/api/procedures/mcr_curve/plots", json={"plot": counts}).json()
    assert saved["origin"] == "workspace"  # the built-in is overridden, not edited
    assert [p["name"] for p in saved["plots"]][-1] == "Raw counts"
    assert saved["plots"][-1]["runs"] == []  # a procedure's plot is for any run

    # Every mcr_curve run, past ones included, now offers it.
    detail = client.get(f"/api/data/runs/{_ids(client)['mcr_a']}").json()
    assert "Raw counts" in [p["name"] for p in detail["plots"]]

    # Saving under an existing name replaces it.
    replaced = client.post(
        "/api/procedures/mcr_curve/plots", json={"plot": {**counts, "log_y": True}, "replace": "Raw counts"}
    ).json()
    assert [p["name"] for p in replaced["plots"]].count("Raw counts") == 1
    assert replaced["plots"][-1]["log_y"] is True


def test_saving_a_plot_needs_a_name_and_a_procedure(client):
    nameless = client.post("/api/procedures/mcr_curve/plots", json={"plot": {"x": "attenuation_db", "y": ["count_rate"]}})
    assert nameless.status_code == 422
    missing = client.post("/api/procedures/nothing/plots", json={"plot": {"name": "x", "x": "a", "y": ["b"]}})
    assert missing.status_code == 404
    broken = client.post("/api/procedures/mcr_curve/plots", json={"plot": {"name": "x", "x": "attenuation_db", "y": ["nope"]}})
    assert broken.status_code == 422


# --------------------------- taking a run elsewhere ---------------------------


def test_a_run_exports_as_a_zipped_folder(client):
    run_id = _ids(client)["mcr_a"]
    response = client.post(f"/api/data/runs/{run_id}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"run{run_id}_mcr_curve_A7.zip" in response.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(response.content)).namelist()
    assert f"run{run_id}_mcr_curve_A7/points.csv" in names
    assert f"run{run_id}_mcr_curve_A7/run.yaml" in names


# --------------------------- devices ---------------------------


def test_devices_are_listed_with_their_runs(client):
    devices = client.get("/api/data/devices").json()["devices"]
    assert [(d["name"], d["runs"]) for d in devices] == [("A7", 2), ("B2", 1)]


def test_a_device_property_becomes_a_filter_on_its_past_runs(client):
    saved = client.put("/api/data/devices/A7", json={"properties": {"wafer": "W12", "width_nm": 80}}).json()
    assert saved["runs"] == 2

    body = client.get("/api/data/facets").json()
    assert {v["value"]: v["runs"] for v in _facet(body, "device.wafer")["values"]} == {"W12": 2}
    assert _facet(body, "device.wafer")["group"] == "Device properties"
    runs = client.get("/api/data/runs", params=_filters(**{"device.wafer": "W12"})).json()
    assert {r["project"] for r in runs["runs"]} == {"mcr_a", "probe"}

    # Replacing the properties removes the old ones from the filters.
    client.put("/api/data/devices/A7", json={"properties": {"wafer": "W13"}})
    body = client.get("/api/data/facets").json()
    assert {v["value"] for v in _facet(body, "device.wafer")["values"]} == {"W13"}
    assert all(f["key"] != "device.width_nm" for f in body["facets"])


def test_a_device_can_be_registered_before_it_is_measured(client):
    client.put("/api/data/devices/C3", json={"properties": {"wafer": "W14"}, "notes": "spare"})
    devices = {d["name"]: d for d in client.get("/api/data/devices").json()["devices"]}
    assert devices["C3"] == {"name": "C3", "properties": {"wafer": "W14"}, "notes": "spare", "runs": 0}


def test_a_device_property_is_one_named_value(client):
    assert client.put("/api/data/devices/A7", json={"properties": {"a.b": 1}}).status_code == 422
    assert client.put("/api/data/devices/A7", json={"properties": {"size": {"w": 1}}}).status_code == 422
