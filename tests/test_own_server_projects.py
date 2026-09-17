"""A procedure project that uses its instruments through this workspace's server.

The case the procedure plan started from: the wizard has started the
workspace's server, and a measurement is created with its instruments picked
from "This workspace, through its server" instead of opened locally. Everything
here is the real thing — the server process ``start_server`` launches, the
wizard's HTTP API, and the generated setup file run as a script.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
from fastapi.testclient import TestClient
from ruamel.yaml import YAML

from lab_wizard.lib.client.session import Session
from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900Params
from lab_wizard.lib.instruments.fake_rack.fake_attenuator import FakeAttenuatorParams
from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounterParams
from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpibParams
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928Params
from lab_wizard.lib.instruments.fake_rack.modules.fake970 import Fake970Params
from lab_wizard.lib.procedures.storage import load_procedure, save_preset
from lab_wizard.lib.savers.database_saver import DatabaseSaverParams
from lab_wizard.lib.utilities.flat_resource_io import save_resource
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend.main import app
from lab_wizard.wizard.backend.server_control import set_server_bind, start_server, stop_server
from lab_wizard.wizard.workspace import WORKSPACE_ENV, initialize_workspace

RACK = instrument_hash("fakegpib", "sim://own-rack")
MAINFRAME = instrument_hash("fake900", "5")
SOURCE = instrument_hash("fake928", "1")
METER = instrument_hash("fake970", "2")
COUNTER = instrument_hash("fake_counter", "sim://own-counter:5025")
ATTENUATOR = instrument_hash("fake_attenuator", "sim://own-attenuator")


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def served(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[Any, TestClient]]:
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "registry"))
    ws, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    instruments: dict[str, Any] = {
        RACK: FakeGpibParams(
            port="sim://own-rack",
            children={
                MAINFRAME: Fake900Params(
                    gpib_address="5",
                    detector_name="own",
                    children={SOURCE: Fake928Params(slot="1"), METER: Fake970Params(slot="2")},
                )
            },
        ),
        COUNTER: FakeCounterParams(ip_address="sim://own-counter", detector_name="own"),
        ATTENUATOR: FakeAttenuatorParams(port="sim://own-attenuator", detector_name="own"),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, ws.config_dir)
    definition = load_procedure(ws.config_dir, "mcr_curve")
    save_preset(
        ws.config_dir,
        "mcr_curve",
        "quick",
        {
            "bias": {"voltage": 0.026, "settle_s": 0.0},
            "attenuation": {"sweep": {"mode": "explicit", "values": [10.0, 0.0]}, "settle_s": 0.0},
            "readout": {"gate_time_s": 0.02, "threshold_mV": -50.0},
        },
        definition.params_model(),
    )

    save_resource(ws.config_dir, "saver", "db", DatabaseSaverParams())

    set_server_bind(ws.config_dir, f"tcp://127.0.0.1:{_free_port()}")
    start_server(ws.config_dir, detached=False)
    monkeypatch.setattr(app.state, "env", None, raising=False)
    try:
        yield ws, TestClient(app)
    finally:
        stop_server(ws.config_dir)


def _own_server(client: TestClient) -> dict[str, Any]:
    deadline = time.monotonic() + 15
    while True:  # the server advertises itself once bound
        listing = client.get("/api/instrument-sources").json()
        own = [s for s in listing["sources"] if s.get("is_own_server") and s.get("reachable")]
        if own:
            return own[0]
        if time.monotonic() > deadline:
            raise AssertionError(f"the workspace's server never appeared: {listing}")
        time.sleep(0.2)


def _routed(own: dict[str, Any], role: str, behavior: str) -> dict[str, Any]:
    matches = sorted(
        (a for a in own["attributes"] if a["behavior_abc"] == behavior), key=lambda a: a["path"]
    )
    assert matches, f"{own['name']} offers no {behavior}: {own['attributes']}"
    return {"variable_name": role, "source": own["name"], "attribute": matches[0]["attribute_name"]}


def test_a_procedure_run_through_the_workspaces_own_server(served):
    ws, client = served
    own = _own_server(client)
    selections = [
        _routed(own, "voltage_source", "VSource"),
        _routed(own, "voltage_sense", "VSense"),
        _routed(own, "counter", "Counter"),
        _routed(own, "attenuator", "Attenuator"),
    ]
    created = client.post(
        "/api/create-measurement-project",
        json={
            "measurement_name": "mcr_curve",
            "kind": "procedure",
            "params_preset": "quick",
            "selected_resources": [
                *selections,
                {"variable_name": "savers", "resource_kind": "saver", "type": "database_saver", "key": "db"},
            ],
        },
    )
    assert created.status_code == 200, created.text
    out = created.json()

    payload = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    assert "instruments" not in payload["resources"]
    assert set(payload["resources"]["instrument_sources"].values()) == {own["name"]}

    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr

    # The data came back through the server: a background count, then one count
    # and one voltage per attenuation.
    with sqlite3.connect(setup_path.parent / "measurements.db") as db:
        rows = [json.loads(data) for (data,) in db.execute("select data from measurements order by id")]
        (recorded,) = db.execute("select instruments from runs").fetchone()

    # Provenance (procedure plan 5.6): what the instruments were configured
    # with, read back through the server that owns them, since this workspace's
    # project carries no copy of it.
    configured = json.loads(recorded)
    assert set(configured) == set(payload["resources"]["instrument_sources"])
    assert configured[selections[3]["attribute"]]["type"] == "fake_attenuator"
    background = [r for r in rows if r["phase"] == "background"]
    counts = [r for r in rows if r["phase"] == "signal" and "counts" in r]
    voltages = [r for r in rows if r["phase"] == "signal" and "device_voltage" in r]
    assert len(background) == 1
    assert [r["attenuation_db"] for r in counts] == [10.0, 0.0]
    assert background[0]["counts"] < counts[0]["counts"] < counts[1]["counts"]
    assert [r["attenuation_db"] for r in voltages] == [10.0, 0.0]

    # The run handed everything back: no claims left, and the attenuator the
    # procedure closed and fully attenuated is what the server now reports.
    session = Session(own["url"], timeout_ms=5000)
    try:
        deadline = time.monotonic() + 5
        while session.call("claim_list") and time.monotonic() < deadline:
            time.sleep(0.05)
        assert session.call("claim_list") == []
        attenuator = next(a for a in own["attributes"] if a["behavior_abc"] == "Attenuator")["path"]
        assert session.call_inst(attenuator, "get_attenuation") == session.call_inst(
            attenuator, "get_max_attenuation"
        )
    finally:
        session.close()

