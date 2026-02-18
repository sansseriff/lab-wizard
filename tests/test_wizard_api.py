"""Tests for the wizard backend API endpoints.

These tests hit the FastAPI endpoints directly using TestClient,
without starting an actual server.
"""

import pytest
from fastapi.testclient import TestClient

from lab_wizard.wizard.backend.main import app


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
        var_names: list[str] = [req["variable_name"] for req in data]
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
