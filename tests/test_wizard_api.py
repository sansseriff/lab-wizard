"""Tests for the wizard backend API endpoints.

These tests hit the FastAPI endpoints directly using TestClient,
without starting an actual server.
"""

import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from typing import Any, cast

from lab_wizard.wizard.backend.main import app
import lab_wizard.wizard.backend.main as backend_main
from lab_wizard.wizard.backend.models import Env
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import save_instruments_to_config


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    with TestClient(app) as c:
        yield c


class TestGetMeasurements:
    """Tests for /api/get-measurements endpoint."""

    def test_returns_dict(self, client: TestClient):
        """Endpoint should return a dict of measurements."""
        response = client.get("/api/get-measurements")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, dict)

    def test_contains_iv_curve(self, client: TestClient):
        """The iv_curve measurement should be discovered."""
        response = client.get("/api/get-measurements")
        assert response.status_code == 200
        data = response.json()
        assert "iv_curve" in data

    def test_measurement_info_structure(self, client: TestClient):
        """Each measurement should have required fields."""
        response = client.get("/api/get-measurements")
        data = response.json()

        for name, info in data.items():
            assert "name" in info, f"Missing 'name' in {name}"
            assert "description" in info, f"Missing 'description' in {name}"
            assert "measurement_dir" in info, f"Missing 'measurement_dir' in {name}"
            assert info["name"] == name


class TestGetInstruments:
    """Tests for /api/get-instruments/{name} endpoint."""

    def test_iv_curve_instruments(self, client: TestClient):
        """iv_curve should require voltage_source and voltage_sense."""
        response = client.get("/api/get-instruments/iv_curve")
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
        response = client.get("/api/get-instruments/iv_curve")
        data = response.json()

        for req in data:
            assert "variable_name" in req
            assert "base_type" in req
            assert "matching_instruments" in req
            assert isinstance(req["matching_instruments"], list)

    def test_matching_instruments_found(self, client: TestClient):
        """Instrument discovery should find matching implementations."""
        response = client.get("/api/get-instruments/iv_curve")
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
        response = client.get("/api/get-instruments/iv_curve")
        assert response.status_code == 200
        data = response.json()
        vsource_req = next((r for r in data if r["variable_name"] == "voltage_source"), None)
        assert vsource_req is not None
        class_names = [m["class_name"] for m in vsource_req["matching_instruments"]]
        assert "Dac4DChannel" in class_names
        assert "Dac16DChannel" in class_names

    def test_voltage_sense_includes_sim970_channel(self, client: TestClient):
        """VSense discovery should include channel-level implementations like Sim970Channel."""
        response = client.get("/api/get-instruments/iv_curve")
        assert response.status_code == 200
        data = response.json()
        vsense_req = next((r for r in data if r["variable_name"] == "voltage_sense"), None)
        assert vsense_req is not None
        class_names = [m["class_name"] for m in vsense_req["matching_instruments"]]
        assert "Sim970Channel" in class_names

    def test_counter_includes_keysight_channel(self, client: TestClient):
        """Counter discovery should include Keysight channel implementation."""
        response = client.get("/api/get-instruments/pcr_curve")
        assert response.status_code == 200
        data = response.json()
        counter_req = next((r for r in data if r["variable_name"] == "counter"), None)
        assert counter_req is not None
        class_names = [m["class_name"] for m in counter_req["matching_instruments"]]
        assert "Keysight53220AChannel" in class_names

    def test_unknown_measurement_returns_404(self, client: TestClient):
        """Requesting instruments for unknown measurement should return 404."""
        response = client.get("/api/get-instruments/nonexistent_measurement")
        assert response.status_code == 404


class TestResourcesMeta:
    """Tests for /api/resources/meta endpoint."""

    def test_returns_types(self, client: TestClient):
        """Endpoint should return resource type options."""
        response = client.get("/api/resources/meta")
        assert response.status_code == 200
        data = response.json()
        assert "types" in data
        assert isinstance(data["types"], list)


def _seed_config(config_dir: Path) -> None:
    instruments = {
        "/dev/ttyUSB0": PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                "5": Sim900Params(
                    children={
                        "1": Sim928Params(),
                        "2": Sim970Params(),
                    }
                )
            },
        )
    }
    save_instruments_to_config(instruments, config_dir)


class TestCreateMeasurementProject:
    def test_creates_project_folder(self, client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg = tmp_path / "config"
        prj = tmp_path / "projects"
        _seed_config(cfg)
        prj.mkdir(parents=True, exist_ok=True)

        def _cfg_override(_env: Env) -> str:
            return str(cfg)

        def _prj_override(_env: Env) -> Path:
            return prj

        monkeypatch.setattr(backend_main, "_config_dir", _cfg_override)
        monkeypatch.setattr(backend_main, "_projects_dir", _prj_override)

        body = {
            "measurement_name": "iv_curve",
            "project_prefix": "api_test",
            "selected_resources": [
                {
                    "variable_name": "voltage_source",
                    "type": "sim928",
                    "key": "1",
                    "path": [
                        {"type": "sim928", "key": "1"},
                        {"type": "sim900", "key": "5"},
                        {"type": "prologix_gpib", "key": "/dev/ttyUSB0"},
                    ],
                },
                {
                    "variable_name": "voltage_sense",
                    "type": "sim970",
                    "key": "2",
                    "channel_index": 0,
                    "path": [
                        {"type": "sim970", "key": "2"},
                        {"type": "sim900", "key": "5"},
                        {"type": "prologix_gpib", "key": "/dev/ttyUSB0"},
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
