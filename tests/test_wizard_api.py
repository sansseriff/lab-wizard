"""Tests for the wizard backend API endpoints.

These tests hit the FastAPI endpoints directly using TestClient,
without starting an actual server.
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from typing import Any, cast

from lab_wizard.lib.data.settings import load_data_settings
from lab_wizard.wizard.backend.main import app
from lab_wizard.wizard.backend.deps import get_env
from lab_wizard.wizard.backend.models import Env
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace

_PROLOGIX_KEY = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
_SIM900_KEY = instrument_hash("sim900", "5")
_SIM928_KEY = instrument_hash("sim928", "1")
_SIM970_KEY = instrument_hash("sim970", "2")


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Create a test client for the FastAPI app."""
    workspace, _ = initialize_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ENV, str(workspace.root))
    with TestClient(app) as c:
        yield c


class TestMeasurementChoices:
    """Tests for /api/measurement-choices."""

    def test_iv_and_pcr_curves_are_offered_as_built_in_procedures(self, client: TestClient):
        """They were hand-written measurements; now they are procedures like mcr_curve."""
        response = client.get("/api/measurement-choices")
        assert response.status_code == 200
        choices = {(c["name"], c["kind"]): c for c in response.json()["choices"]}
        for name in ("iv_curve", "pcr_curve", "mcr_curve"):
            assert choices[(name, "procedure")]["origin"] == "builtin"
        assert not any(kind == "measurement" for _name, kind in choices)

class TestGetResources:
    """Tests for /api/get-resources/{name} endpoint."""

    def test_iv_curve_instruments(self, client: TestClient):
        """iv_curve should require voltage_source and voltage_sense."""
        response = client.get("/api/get-resources/iv_curve?kind=procedure")
        assert response.status_code == 200
        data = response.json()

        # Should return a list of requirements
        assert isinstance(data, list)

        # Extract variable names
        data_list = cast(list[dict[str, Any]], data)
        var_names: list[str] = [str(req["variable_name"]) for req in data_list]
        assert "voltage_source" in var_names
        assert "voltage_sense" in var_names

    def test_instrument_req_structure(self, client: TestClient):
        """Each instrument requirement should have the expected fields."""
        response = client.get("/api/get-resources/iv_curve?kind=procedure")
        data = response.json()

        for req in data:
            assert "variable_name" in req
            assert "base_type" in req
            assert "matching_instruments" in req
            assert isinstance(req["matching_instruments"], list)

    def test_matching_instruments_found(self, client: TestClient):
        """Instrument discovery should find matching implementations."""
        response = client.get("/api/get-resources/iv_curve?kind=procedure")
        data = response.json()

        # Find the voltage_source requirement
        vsource_req = next(
            (r for r in data if r["variable_name"] == "voltage_source"), None
        )
        assert vsource_req is not None

        # Should have found at least one matching instrument (Sim928, Dac4D, etc.)
        matches = vsource_req["matching_instruments"]
        assert len(matches) > 0, "Expected to find matching instruments for VSource"

        # Each match should have the required fields
        for match in matches:
            assert "module" in match
            assert "class_name" in match
            assert "qualname" in match
            assert "file_path" in match
            assert "friendly_name" in match

    def test_voltage_source_includes_dbay_channels(self, client: TestClient):
        """VSource discovery should include DBay channel-level implementations."""
        response = client.get("/api/get-resources/iv_curve?kind=procedure")
        assert response.status_code == 200
        data = response.json()
        vsource_req = next((r for r in data if r["variable_name"] == "voltage_source"), None)
        assert vsource_req is not None
        class_names = [m["class_name"] for m in vsource_req["matching_instruments"]]
        assert "Dac4DChannel" in class_names
        assert "Dac16DChannel" in class_names

    def test_voltage_sense_includes_sim970_channel(self, client: TestClient):
        """VSense discovery should include channel-level implementations like Sim970Channel."""
        response = client.get("/api/get-resources/iv_curve?kind=procedure")
        assert response.status_code == 200
        data = response.json()
        vsense_req = next((r for r in data if r["variable_name"] == "voltage_sense"), None)
        assert vsense_req is not None
        class_names = [m["class_name"] for m in vsense_req["matching_instruments"]]
        assert "Sim970Channel" in class_names

    def test_counter_includes_keysight_channel(self, client: TestClient):
        """Counter discovery should include Keysight channel implementation."""
        response = client.get("/api/get-resources/pcr_curve?kind=procedure")
        assert response.status_code == 200
        data = response.json()
        counter_req = next((r for r in data if r["variable_name"] == "counter"), None)
        assert counter_req is not None
        class_names = [m["class_name"] for m in counter_req["matching_instruments"]]
        assert "Keysight53220AChannel" in class_names

    def test_unknown_measurement_returns_404(self, client: TestClient):
        """Requesting instruments for unknown measurement should return 404."""
        response = client.get("/api/get-resources/nonexistent_measurement")
        assert response.status_code == 404


class TestFileSettings:
    """/api/settings/files: how runs are saved as files, for the whole workspace."""

    def test_defaults_before_anything_is_saved(self, client: TestClient, tmp_path: Path):
        data = client.get("/api/settings/files").json()
        assert data["files"] == {"root": "", "path": "{date}/{procedure}_{device}_{time}", "plot_png": True}
        assert data["folder"] == str((tmp_path / "workspace" / "data" / "files").resolve())
        assert data["example"] == "2026-09-22/mcr_curve_A7_143012"

    def test_saved_settings_are_the_workspaces(self, client: TestClient, tmp_path: Path):
        response = client.put(
            "/api/settings/files",
            json={"root": "runs", "path": "{device.wafer}/{device}/{date}", "plot_png": False},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["folder"] == str((tmp_path / "workspace" / "runs").resolve())
        assert data["example"] == "W12/A7/2026-09-22"

        config_dir = tmp_path / "workspace" / "config"
        assert load_data_settings(config_dir).files.path == "{device.wafer}/{device}/{date}"
        assert client.get("/api/settings/files").json()["files"]["plot_png"] is False

    def test_the_workspace_paths_are_the_manifests(self, client: TestClient, tmp_path: Path):
        paths = client.get("/api/settings/workspace").json()
        root = (tmp_path / "workspace").resolve()
        assert paths["root"] == str(root)
        assert paths["database"] == str(root / "data" / "lab.db")
        assert paths["projects_dir"] == str(root / "projects")

    def test_a_template_is_checked_as_it_is_typed(self, client: TestClient):
        check = client.post("/api/settings/files/check", json={"path": "{date}/{devce}"}).json()
        assert [(p["level"], p["key"]) for p in check["problems"]] == [("error", "devce")]
        # A family key no run has recorded yet may still be recorded later.
        check = client.post("/api/settings/files/check", json={"path": "{run.cryostat}/{device}"}).json()
        assert [(p["level"], p["key"]) for p in check["problems"]] == [("warning", "run.cryostat")]
        assert check["example"] == "none/A7"

    def test_a_template_with_an_unknown_key_is_not_saved(self, client: TestClient):
        response = client.put("/api/settings/files", json={"root": "", "path": "{devce}", "plot_png": True})
        assert response.status_code == 422
        assert "devce" in response.json()["detail"]
        assert client.get("/api/settings/files").json()["files"]["path"] == "{date}/{procedure}_{device}_{time}"

    def test_an_empty_template_is_refused(self, client: TestClient):
        response = client.put("/api/settings/files", json={"root": "", "path": " ", "plot_png": True})
        assert response.status_code == 422
        assert "empty" in response.json()["detail"]


def _seed_config(config_dir: Path) -> None:
    instruments = {
        _PROLOGIX_KEY: PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                _SIM900_KEY: Sim900Params(
                    gpib_address="5",
                    children={
                        _SIM928_KEY: Sim928Params(slot="1"),
                        _SIM970_KEY: Sim970Params(slot="2"),
                    },
                )
            },
        )
    }
    # Mirror the wizard CRUD flow: config/instruments is always saved with
    # every hardware channel present and named.
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


class TestCreateMeasurementProject:
    def test_creates_project_folder(self, client: TestClient, tmp_path: Path, request: pytest.FixtureRequest):
        cfg = tmp_path / "config"
        prj = tmp_path / "projects"
        _seed_config(cfg)
        prj.mkdir(parents=True, exist_ok=True)

        app.dependency_overrides[get_env] = lambda: Env(config_dir=cfg, projects_dir=prj)
        request.addfinalizer(app.dependency_overrides.clear)

        body = {
            "measurement_name": "iv_curve",
            "kind": "procedure",
            "project_prefix": "api_test",
            "selected_resources": [
                {
                    "variable_name": "voltage_source",
                    "type": "sim928",
                    "key": _SIM928_KEY,
                    "path": [
                        {"type": "sim928", "key": _SIM928_KEY},
                        {"type": "sim900", "key": _SIM900_KEY},
                        {"type": "prologix_gpib", "key": _PROLOGIX_KEY},
                    ],
                },
                {
                    "variable_name": "voltage_sense",
                    "type": "sim970",
                    "key": _SIM970_KEY,
                    "channel_index": 0,
                    "path": [
                        {"type": "sim970", "key": _SIM970_KEY},
                        {"type": "sim900", "key": _SIM900_KEY},
                        {"type": "prologix_gpib", "key": _PROLOGIX_KEY},
                    ],
                },
            ],
        }
        response = client.post("/api/create-measurement-project", json=body)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert Path(data["yaml_file"]).exists()
        assert Path(data["setup_file"]).exists()
