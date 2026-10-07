"""Setups: each experiment's current facts, copied into every run taken on it.

A procedure that needs a fact to draw its plots declares a need; a measurement
binds the need to one of its setup's fields; a run does not start until every
need reads as a number in its unit (``plans/setup_plan.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from lab_wizard.lib.data import open_database
from lab_wizard.lib.data.setups import SetupError, field_leaves, need_value, resolve_needs, save_setup
from lab_wizard.lib.data.units import UnitError, base_unit, convert, in_base
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace
from lab_wizard.wizard.backend.main import app

# --------------------------- units ---------------------------


def test_a_prefixed_unit_converts_within_its_base_unit():
    assert in_base(4.7, "kΩ") == 4700.0  # not 4700.000000000001
    assert convert(100, "kohm", "ohm") == 100_000.0
    assert convert(250, "mV", "V") == 0.25
    assert convert(3, "MHz", "kHz") == 3000.0
    assert base_unit("µA") == "A" and base_unit("dBm") == "dBm"
    assert convert(-30, "dBm", "dBm") == -30.0  # an unknown unit converts to itself
    with pytest.raises(UnitError):
        convert(1, "V", "Ω")
    with pytest.raises(UnitError):
        convert(1, "dBm", "W")


# --------------------------- fields and needs ---------------------------


FIELDS = {
    "cryostat": "BlueFors1",
    "channel2": {"bias_resistor": {"value": 10, "unit": "kΩ"}, "balun": "BAL-0006"},
    "qcl_power": {"value": 12, "unit": "mW"},
    "wiring": {"image": "3f9a.jpg"},
}


def test_a_need_reads_its_bound_field_in_the_unit_it_declares():
    assert need_value(FIELDS, "channel2.bias_resistor", "ohm") == 10_000.0
    with pytest.raises(SetupError, match="no field channel1.bias_resistor"):
        need_value(FIELDS, "channel1.bias_resistor", "ohm")
    with pytest.raises(SetupError, match="balun is not a number"):
        need_value(FIELDS, "channel2.balun", "ohm")
    with pytest.raises(SetupError, match="in mW, which is not ohm"):
        need_value(FIELDS, "qcl_power", "ohm")


def test_every_declared_need_is_read_or_said_why_not():
    declared = {"bias_resistance": {"unit": "ohm"}, "power": {"unit": "W"}}
    values, problems = resolve_needs(FIELDS, {"bias_resistance": "channel2.bias_resistor"}, declared)
    assert values == {"bias_resistance": 10_000.0}
    assert problems == {"power": "power is not bound to a setup field"}


def test_a_setups_leaves_are_what_a_need_can_be_bound_to():
    leaves = {leaf["path"]: leaf for leaf in field_leaves(FIELDS)}
    assert list(leaves) == ["cryostat", "channel2.bias_resistor", "channel2.balun", "qcl_power", "wiring"]
    assert (leaves["channel2.bias_resistor"]["base_unit"], leaves["channel2.bias_resistor"]["number"]) == ("Ω", 10_000.0)
    assert leaves["cryostat"]["number"] is None


def test_a_setup_refuses_what_it_cannot_hold(tmp_path: Path):
    lab = open_database(tmp_path / "lab.db")
    with pytest.raises(SetupError, match="without dots"):
        save_setup(lab, "bench", {"a.b": 1})
    with pytest.raises(SetupError, match="a quantity's value is a number"):
        save_setup(lab, "bench", {"r": {"value": "ten", "unit": "Ω"}})
    with pytest.raises(SetupError, match="register it under Data"):
        save_setup(lab, "bench", {}, device="A7")
    assert save_setup(lab, "bench", FIELDS)["fields"] == FIELDS


# --------------------------- the API ---------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rig):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    rig.write(ws.config_dir, ("source", "meter"))
    monkeypatch.setattr(app.state, "env", None, raising=False)  # see test_measurement_creation_api
    yield ws, TestClient(app)


def test_a_setup_is_created_edited_and_forgotten(workspace):
    _ws, client = workspace
    assert client.get("/api/setups").json() == []
    client.put("/api/data/devices/A7", json={"properties": {}})
    saved = client.put("/api/setups/mid-ir-bench", json={"fields": FIELDS, "device": "A7", "notes": "the 4 µm line"})
    assert saved.status_code == 200, saved.text
    assert saved.json() | {"fields": None} == {
        "name": "mid-ir-bench", "notes": "the 4 µm line", "fields": None, "device": "A7",
        "runs": 0, "last_run": None, "projects": [],
    }
    assert [s["name"] for s in client.get("/api/setups").json()] == ["mid-ir-bench"]
    assert client.put("/api/setups/mid-ir-bench", json={"device": "Z9"}).status_code == 422
    assert client.delete("/api/setups/mid-ir-bench").status_code == 200
    assert client.get("/api/setups/mid-ir-bench").status_code == 404


def test_a_picture_is_kept_by_what_is_in_it(workspace):
    ws, client = workspace
    png = b"\x89PNG\r\n\x1a\n" + b"pretend"
    first = client.put("/api/setup-images", params={"suffix": ".png"}, content=png).json()["image"]
    again = client.put("/api/setup-images", params={"suffix": "png"}, content=png).json()["image"]
    assert first == again and first.endswith(".png")
    assert client.get(f"/api/setup-images/{first}").content == png
    assert (ws.data_dir / "setup_images" / first).is_file()
    assert client.put("/api/setup-images", params={"suffix": ".exe"}, content=b"x").status_code == 422
    assert client.get("/api/setup-images/..%2Flab.db").status_code == 404


def _create_iv(client: TestClient, rig, setup: dict | None) -> tuple[int, dict]:
    response = client.post("/api/create-measurement-project", json={
        "measurement_name": "iv_curve",
        "kind": "procedure",
        "selected_resources": [
            s.model_dump(mode="json")
            for s in (rig.select("voltage_source", "source"), rig.select("voltage_sense", "meter"))
        ],
        "outputs": {"files": False},
        **({"setup": setup} if setup is not None else {}),
    })
    return response.status_code, response.json()


def test_a_measurement_binds_its_procedures_needs_to_its_setups_fields(workspace, rig):
    ws, client = workspace
    choices = client.get("/api/measurement-choices").json()["choices"]
    iv = next(c for c in choices if c["name"] == "iv_curve")
    assert iv["needs"] == {"bias_resistance": {"unit": "ohm", "description": "the bias resistor the current is inferred through"}}

    client.put("/api/setups/bench", json={"fields": FIELDS})
    binding = {"name": "bench", "needs": {"bias_resistance": "channel2.bias_resistor"}}
    status, out = _create_iv(client, rig, binding)
    assert status == 200, out
    payload = yaml.safe_load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["setup"] == binding
    assert client.get("/api/setups/bench").json()["projects"] == [out["project_name"]]

    # A binding to a field that is not a resistance, or a setup that does not
    # exist, writes no project.
    status, out = _create_iv(client, rig, {"name": "bench", "needs": {"bias_resistance": "qcl_power"}})
    assert status == 400 and "qcl_power is in mW, which is not ohm" in out["detail"]
    status, out = _create_iv(client, rig, {"name": "nowhere", "needs": {}})
    assert status == 400 and "no setup named 'nowhere'" in out["detail"]
    assert len(list(ws.projects_dir.iterdir())) == 1


def test_a_run_with_an_unbound_need_is_refused_before_it_starts(workspace, rig):
    _ws, client = workspace
    status, out = _create_iv(client, rig, None)  # bound later, on the Run page
    assert status == 200, out
    project = out["project_name"]
    settings = client.get(f"/api/projects/{project}/settings").json()
    assert settings["needs"]["bias_resistance"]["unit"] == "ohm"

    refused = client.post(f"/api/projects/{project}/launch")
    assert refused.status_code == 409
    assert "bias_resistance is not bound to a setup field" in refused.json()["detail"]
    assert client.get(f"/api/projects/{project}/launch").json()["state"] == "idle"

    client.put("/api/setups/bench", json={"fields": FIELDS})
    saved = client.put(f"/api/projects/{project}/settings", json={
        "setup": {"name": "bench", "needs": {"bias_resistance": "channel2.bias_resistor"}},
    })
    assert saved.status_code == 200, saved.text
    assert saved.json()["setup"]["needs"] == {"bias_resistance": "channel2.bias_resistor"}
