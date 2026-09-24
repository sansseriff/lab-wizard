"""The Procedures section's API (procedure plan Phase 4).

The composer builds a definition a piece at a time, so most of what it sends
is wrong: these tests pin that every problem comes back with the path of the
step it is about, that nothing unsaveable is saved, and that what is saved is
offered for measurement creation straight away.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lab_wizard.lib.instruments.fake_rack.fake_attenuator import FakeAttenuatorParams
from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounterParams
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend.main import app
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    instruments: dict[str, Any] = {
        instrument_hash("fake_counter", "sim://p-counter:5025"): FakeCounterParams(ip_address="sim://p-counter"),
        instrument_hash("fake_attenuator", "sim://p-att"): FakeAttenuatorParams(port="sim://p-att"),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, ws.config_dir)
    # See test_measurement_creation_api: no lifespan, and no cached workspace.
    monkeypatch.setattr(app.state, "env", None, raising=False)
    yield TestClient(app)


DARK_COUNTS: dict[str, Any] = {
    "name": "dark_counts",
    "description": "Count with the shutter closed",
    "roles": {"counter": {"behavior": "Counter"}, "attenuator": {"behavior": "Attenuator"}},
    "params": {"gate_s": {"type": "float", "default": 1.0, "unit": "s"}},
    "body": {
        "type": "safe_guard",
        "instrument": {"role": "attenuator"},
        "body": {
            "type": "sequence",
            "children": [
                {"type": "close_shutter", "attenuator": {"role": "attenuator"}},
                {"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "gate_s"}},
            ],
        },
    },
}


def _check(client: TestClient, definition: dict[str, Any]) -> dict[str, Any]:
    response = client.post("/api/procedures/check", json={"definition": definition})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------- catalog ---------------------------


def test_the_catalog_describes_steps_behaviors_and_who_can_fill_them(client: TestClient):
    catalog = client.get("/api/procedures/catalog").json()

    count = catalog["steps"]["count"]
    assert count["group"] == "instruments"
    assert (count["fields"]["counter"]["kind"], count["fields"]["counter"]["requires"]) == ("role", ["Counter"])
    assert count["fields"]["gate_time"]["kind"] == "value"
    assert catalog["steps"]["sweep"]["fields"]["values"]["kind"] == "values"
    assert catalog["steps"]["sweep"]["fields"]["parameter"]["column"] == "records"
    assert catalog["steps"]["value_above"]["fields"]["field"]["column"] == "reads"
    assert catalog["steps"]["with_settings"]["fields"]["overrides"]["kind"] == "value_map"
    assert catalog["steps"]["if"]["fields"]["otherwise"]["optional"] is True

    assert catalog["behaviors"]["Counter"]["bindable"] is True
    assert catalog["behaviors"]["ChannelProvider"]["bindable"] is False
    # The fake counter has two inputs; one attenuator.
    assert len(catalog["fillers"]["Counter"]) == 2
    assert len(catalog["fillers"]["Attenuator"]) == 1
    assert catalog["fillers"]["VSource"] == []


# --------------------------- checking ---------------------------


def test_a_complete_definition_checks_and_shows_its_python(client: TestClient):
    result = _check(client, DARK_COUNTS)
    assert result["ok"] is True
    assert result["problems"] == []
    assert result["records"] == ["counts", "int_time", "count_rate"]
    assert "def build_dark_counts_procedure" in result["python"]
    assert "CloseShutter(attenuator=attenuator)" in result["python"]


def test_a_half_built_step_is_reported_at_its_path(client: TestClient):
    definition = copy.deepcopy(DARK_COUNTS)
    definition["body"]["body"]["children"].append({"type": "count", "counter": {"role": "counter"}})

    result = _check(client, definition)
    assert result["ok"] is False
    assert result["python"] is None
    assert result["problems"] == [
        {"path": ["body", "body", "children", 2, "gate_time"], "message": "Field required"}
    ]


def test_problems_found_by_checking_are_located_too(client: TestClient):
    definition = copy.deepcopy(DARK_COUNTS)
    children = definition["body"]["body"]["children"]
    children[0]["attenuator"] = {"role": "counter"}  # wrong behavior
    children[1]["gate_time"] = {"param": "readout.gate_s"}  # undeclared
    children.append({"type": "set_voltage", "source": {"role": "bias"}, "voltage": 0.0})  # undeclared role

    problems = _check(client, definition)["problems"]
    by_path = {tuple(p["path"]): p["message"] for p in problems}
    assert "needs a Attenuator, but role 'counter' is a Counter" in by_path[("body", "body", "children", 0)]
    assert "'readout.gate_s', which is not declared" in by_path[("body", "body", "children", 1)]
    assert "role 'bias', which the procedure does not declare" in by_path[("body", "body", "children", 2)]


def test_a_condition_on_a_column_nothing_records_is_located(client: TestClient):
    definition = copy.deepcopy(DARK_COUNTS)
    definition["body"]["body"]["children"].append(
        {"type": "value_above", "field": "voltage", "threshold": 1.0}
    )
    problems = _check(client, definition)["problems"]
    assert [p["path"] for p in problems] == [["body", "body", "children", 2]]
    assert "which no step in this procedure records" in problems[0]["message"]


def test_an_unknown_behavior_is_reported_on_its_role(client: TestClient):
    definition = copy.deepcopy(DARK_COUNTS)
    definition["roles"]["counter"]["behavior"] = "Photometer"
    problems = _check(client, definition)["problems"]
    assert ["roles", "counter"] in [p["path"] for p in problems]


# --------------------------- saving ---------------------------


def test_a_saved_procedure_is_listed_and_offered_for_measurement_creation(client: TestClient):
    saved = client.put("/api/procedures/dark_counts", json={"definition": DARK_COUNTS})
    assert saved.status_code == 200, saved.text
    assert saved.json()["origin"] == "workspace"

    listed = {p["name"]: p for p in client.get("/api/procedures").json()["procedures"]}
    assert listed["dark_counts"]["origin"] == "workspace"
    assert listed["dark_counts"]["problems"] == []
    assert listed["dark_counts"]["roles"] == {"counter": "Counter", "attenuator": "Attenuator"}

    detail = client.get("/api/procedures/dark_counts").json()
    assert detail["definition"]["body"]["type"] == "safe_guard"

    choices = client.get("/api/measurement-choices").json()["choices"]
    assert {"name": "dark_counts", "kind": "procedure"}.items() <= next(
        c for c in choices if c["name"] == "dark_counts"
    ).items()


def test_nothing_that_does_not_check_is_saved(client: TestClient):
    broken = copy.deepcopy(DARK_COUNTS)
    broken["body"]["body"]["children"][1]["gate_time"] = {"param": "nope"}
    response = client.put("/api/procedures/dark_counts", json={"definition": broken})
    assert response.status_code == 422
    assert "'nope', which is not declared" in response.json()["detail"]

    half_built = copy.deepcopy(DARK_COUNTS)
    del half_built["body"]["body"]["children"][1]["gate_time"]
    assert client.put("/api/procedures/dark_counts", json={"definition": half_built}).status_code == 422

    assert client.get("/api/procedures/dark_counts").status_code == 404


def test_the_url_and_the_definition_must_agree_on_the_name(client: TestClient):
    response = client.put("/api/procedures/other_name", json={"definition": DARK_COUNTS})
    assert response.status_code == 400


def test_editing_a_built_in_saves_an_override_and_deleting_it_restores_the_built_in(client: TestClient):
    builtin = client.get("/api/procedures/mcr_curve").json()
    assert builtin["origin"] == "builtin"
    assert client.delete("/api/procedures/mcr_curve").status_code == 404  # nothing of ours to delete

    edited = builtin["definition"]
    edited["description"] = "our lab's MCR"
    assert client.put("/api/procedures/mcr_curve", json={"definition": edited}).status_code == 200

    listed = {p["name"]: p for p in client.get("/api/procedures").json()["procedures"]}
    assert listed["mcr_curve"]["origin"] == "workspace"
    assert listed["mcr_curve"]["overrides_builtin"] is True

    restored = client.delete("/api/procedures/mcr_curve").json()
    assert restored["origin"] == "builtin"
    assert client.get("/api/procedures/mcr_curve").json()["definition"]["description"] != "our lab's MCR"


def test_yaml_round_trips_for_hand_editing(client: TestClient):
    text = client.post("/api/procedures/to-yaml", json={"definition": DARK_COUNTS}).json()["yaml"]
    assert "close_shutter" in text
    back = client.post("/api/procedures/from-yaml", json={"yaml": text}).json()["definition"]
    assert back == DARK_COUNTS

    bad = client.post("/api/procedures/from-yaml", json={"yaml": "name: [unclosed"})
    assert bad.status_code == 400


# --------------------------- presets ---------------------------


def test_presets_are_saved_against_the_params_and_deleted(client: TestClient):
    client.put("/api/procedures/dark_counts", json={"definition": DARK_COUNTS})

    assert client.get("/api/procedures/dark_counts/presets").json() == {
        "defaults": {"gate_s": 1.0},
        "presets": {},
        "errors": {},
    }
    saved = client.put("/api/procedures/dark_counts/presets/long", json={"values": {"gate_s": 10.0}})
    assert saved.status_code == 200, saved.text
    assert client.get("/api/procedures/dark_counts/presets").json()["presets"] == {"long": {"gate_s": 10.0}}

    wrong = client.put("/api/procedures/dark_counts/presets/bad", json={"values": {"gate_s": "ten"}})
    assert wrong.status_code == 422

    assert client.delete("/api/procedures/dark_counts/presets/long").status_code == 200
    assert client.delete("/api/procedures/dark_counts/presets/long").status_code == 404
