"""mcr_curve: the first measurement built only as a procedure.

Procedure plan 6.4. The definition ships with lab_wizard
(``lib/procedures/library/mcr_curve.yml``); nothing about it is hand-written
Python. These tests generate a project from it against a simulated rack with a
simulated attenuator in the light path, and check the measured curve against
the detector model: count rate falling as ``10 ** (-dB / 10)``, over a
background taken with the shutter closed.
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lab_procedure import Observation, ProcedureRunner, Status

from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900Params
from lab_wizard.lib.instruments.fake_rack.fake_attenuator import FakeAttenuator, FakeAttenuatorParams
from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounterParams
from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpibParams
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928Params
from lab_wizard.lib.instruments.fake_rack.modules.fake970 import Fake970Params
from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams
from lab_wizard.lib.instruments.fake_rack.wiring import reset_detectors
from lab_wizard.lib.instruments.general.behavior import behavior_name_for
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import (
    delete_procedure,
    list_procedures,
    load_procedure,
    procedure_origin,
    save_preset,
    save_procedure,
)
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.lib.client.project_resources import resource_source_for
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
)

PORT, DETECTOR = "sim://mcr-rack", "snspd-mcr"
COUNTER_ADDRESS, ATTENUATOR_ADDRESS = "sim://mcr-counter", "sim://mcr-attenuator"
GPIB_KEY = instrument_hash("fakegpib", PORT)
MAINFRAME_KEY = instrument_hash("fake900", "5")
SOURCE_KEY = instrument_hash("fake928", "1")
METER_KEY = instrument_hash("fake970", "2")
COUNTER_KEY = instrument_hash("fake_counter", f"{COUNTER_ADDRESS}:5025")
ATTENUATOR_KEY = instrument_hash("fake_attenuator", ATTENUATOR_ADDRESS)

DEVICE = SnspdModelParams()
BIAS_V = 0.026  # on the plateau, below switching
ATTENUATIONS = [20.0, 10.0, 5.0, 0.0]
GATE_S = 0.05
THRESHOLD_MV = -50.0


@pytest.fixture(autouse=True)
def _cold_lab() -> None:
    reset_detectors()


# --------------------------- the simulated attenuator ---------------------------


def test_the_simulated_attenuator_is_an_attenuator_that_dims_the_detector():
    attenuator = FakeAttenuatorParams(port="sim://unit", detector_name="unit-test").create_inst()
    assert isinstance(attenuator, FakeAttenuator)
    assert behavior_name_for(FakeAttenuator, is_class=True) == "Attenuator"

    attenuator.set_attenuation(10.0)
    assert attenuator.model.optical_transmission == pytest.approx(0.1)
    attenuator.close_shutter()
    assert attenuator.model.optical_transmission == 0.0
    attenuator.open_shutter()
    assert attenuator.model.optical_transmission == pytest.approx(0.1)


def test_the_simulated_attenuator_clamps_and_quantizes_like_hardware():
    attenuator = FakeAttenuatorParams(port="sim://unit", detector_name="unit-test", max_attenuation=40.0).create_inst()
    attenuator.set_attenuation(12.34567)
    assert attenuator.get_attenuation() == pytest.approx(12.346)
    attenuator.set_attenuation(99.0)
    assert attenuator.get_attenuation() == 40.0
    assert attenuator.enter_safe_state() is True
    assert attenuator.model.optical_transmission == 0.0


def test_attenuation_leaves_dark_counts_alone():
    model = SnspdModel(DEVICE)
    model.set_output_enabled(True)
    model.set_bias_voltage(BIAS_V)
    model.set_optical_transmission(0.0)
    assert model.count_rate() == pytest.approx(model.dark_count_rate() * model.discriminator_fraction(0.0))


# --------------------------- built-in procedures ---------------------------


def test_mcr_curve_ships_built_in_and_checks(tmp_path: Path):
    config_dir = tmp_path / "config"
    assert "mcr_curve" in list_procedures(config_dir)
    assert procedure_origin(config_dir, "mcr_curve") == "builtin"
    definition = load_procedure(config_dir, "mcr_curve")
    definition.check()
    assert set(definition.roles) == {"voltage_source", "voltage_sense", "counter", "attenuator"}
    assert {"phase", "attenuation_db", "count_rate", "device_voltage"} <= set(definition.emitted_fields())


def test_a_workspace_procedure_overrides_a_built_in_and_deleting_it_restores_it(tmp_path: Path):
    config_dir = tmp_path / "config"
    adapted = load_procedure(config_dir, "mcr_curve").model_copy(update={"description": "our lab's MCR"})
    save_procedure(config_dir, adapted)
    assert procedure_origin(config_dir, "mcr_curve") == "workspace"
    assert load_procedure(config_dir, "mcr_curve").description == "our lab's MCR"

    assert delete_procedure(config_dir, "mcr_curve") is True
    assert procedure_origin(config_dir, "mcr_curve") == "builtin"
    assert delete_procedure(config_dir, "mcr_curve") is False  # a built-in cannot be deleted


# --------------------------- the measurement, end to end ---------------------------


def _write_instruments(config_dir: Path) -> None:
    instruments = {
        GPIB_KEY: FakeGpibParams(
            port=PORT,
            children={
                MAINFRAME_KEY: Fake900Params(
                    gpib_address="5",
                    device=DEVICE,
                    detector_name=DETECTOR,
                    children={SOURCE_KEY: Fake928Params(slot="1"), METER_KEY: Fake970Params(slot="2")},
                )
            },
        ),
        COUNTER_KEY: FakeCounterParams(ip_address=COUNTER_ADDRESS, detector_name=DETECTOR, device=DEVICE),
        ATTENUATOR_KEY: FakeAttenuatorParams(port=ATTENUATOR_ADDRESS, detector_name=DETECTOR, device=DEVICE),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


def _selections() -> list[SelectedResource]:
    rack = [SelectedNodeRef(type="fake900", key=MAINFRAME_KEY), SelectedNodeRef(type="fakegpib", key=GPIB_KEY)]
    return [
        SelectedResource(
            variable_name="voltage_source", type="fake928", key=SOURCE_KEY,
            path=[SelectedNodeRef(type="fake928", key=SOURCE_KEY), *rack],
        ),
        SelectedResource(
            variable_name="voltage_sense", type="fake970", key=METER_KEY, channel_index=0,
            path=[SelectedNodeRef(type="fake970", key=METER_KEY), *rack],
        ),
        SelectedResource(
            variable_name="counter", type="fake_counter", key=COUNTER_KEY, channel_index=0,
            path=[SelectedNodeRef(type="fake_counter", key=COUNTER_KEY)],
        ),
        SelectedResource(
            variable_name="attenuator", type="fake_attenuator", key=ATTENUATOR_KEY,
            path=[SelectedNodeRef(type="fake_attenuator", key=ATTENUATOR_KEY)],
        ),
    ]


def _generate(tmp_path: Path) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    _write_instruments(config_dir)
    definition = load_procedure(config_dir, "mcr_curve")
    save_preset(
        config_dir,
        "mcr_curve",
        "quick",
        {
            "bias": {"voltage": BIAS_V, "settle_s": 0.0},
            "attenuation": {"sweep": {"mode": "explicit", "values": ATTENUATIONS}, "settle_s": 0.0},
            "readout": {"gate_time_s": GATE_S, "threshold_mV": THRESHOLD_MV},
        },
        definition.params_model(),
    )
    return generate_procedure_project(
        config_dir=config_dir,
        projects_dir=tmp_path / "projects",
        req=GenerateProjectRequest(
            measurement_name="mcr_curve", selected_resources=_selections(), params_preset="quick"
        ),
    )


def _load(out: dict[str, Any]) -> tuple[Any, Any]:
    setup_path = Path(out["setup_file"])
    sys.path.insert(0, str(setup_path.parent))
    try:
        spec = importlib.util.spec_from_file_location("mcr_setup", setup_path)
        assert spec is not None and spec.loader is not None
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        return setup, importlib.import_module("mcr_curve")
    finally:
        sys.path.remove(str(setup_path.parent))
        sys.modules.pop("mcr_curve", None)


def _expected_rate(transmission: float) -> float:
    model = SnspdModel(DEVICE)
    model.set_output_enabled(True)
    model.set_bias_voltage(BIAS_V)
    model.set_optical_transmission(transmission)
    return model.count_rate(THRESHOLD_MV)


def test_the_measured_mcr_curve_follows_the_attenuation(tmp_path: Path):
    out = _generate(tmp_path)
    setup, measurement = _load(out)
    resources = setup.create_instrument_resources(
        project := load_project_config(Path(out["yaml_file"])),
        resource_source_for(project, Path(out["project_dir"])),
    )

    runner = ProcedureRunner(instruments=resources)
    rows: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, rows.append)
    assert runner.run(measurement.McrCurveMeasurement(resources).build_procedure()) is Status.SUCCESS

    background = [r.data for r in rows if r.data.get("phase") == "background"]
    counts = [r.data for r in rows if r.data.get("phase") == "signal" and "counts" in r.data]
    voltages = [r.data for r in rows if r.data.get("phase") == "signal" and "device_voltage" in r.data]

    assert len(background) == 1
    expected_dark = _expected_rate(0.0) * GATE_S
    assert background[0]["counts"] <= 5 * math.sqrt(expected_dark) + 5

    assert [row["attenuation_db"] for row in counts] == ATTENUATIONS
    for row in counts:
        expected = _expected_rate(10 ** (-row["attenuation_db"] / 10)) * GATE_S
        assert abs(row["counts"] - expected) <= 5 * math.sqrt(expected) + 5, row
    assert [row["counts"] for row in counts] == sorted(row["counts"] for row in counts)

    # Biased below switching the whole time, so the detector stayed superconducting.
    assert all(abs(row["device_voltage"]) < 1e-3 for row in voltages)

    # The run ended in the attenuator's safe state: shutter closed, fully attenuated.
    assert resources.attenuator.shutter_open is False
    assert resources.attenuator.get_attenuation() == resources.attenuator.get_max_attenuation()


def test_the_generated_mcr_project_runs_as_a_script(tmp_path: Path):
    out = _generate(tmp_path)
    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
