"""mcr_curve: the first measurement built only as a procedure.

Procedure plan 6.4. The definition ships with lab_wizard
(``lib/procedures/library/mcr_curve.yml``); nothing about it is hand-written
Python. These tests generate a project from it against the simulated bench,
with its attenuator in the light path, and check the measured curve against
the detector model: count rate falling as ``10 ** (-dB / 10)``, over a
background taken with the shutter closed.
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import polars as pl
import pytest

from lab_procedure import Point, ProcedureRunner, Status

from lab_sim import SnspdModel, SnspdParams
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import (
    delete_procedure,
    list_procedures,
    load_procedure,
    procedure_origin,
    save_preset,
    save_procedure,
)
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.lib.client.project_resources import resource_source_for
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest

DEVICE = SnspdParams()
BIAS_V = 0.026  # on the plateau, below switching
ATTENUATIONS = [20.0, 10.0, 5.0, 0.0]
GATE_S = 0.05
THRESHOLD_MV = -50.0


# --------------------------- the detector ---------------------------


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


def _selections(rig) -> list:
    return [
        rig.select("voltage_source", "source"),
        rig.select("voltage_sense", "meter"),
        rig.select("counter", "counter"),
        rig.select("attenuator", "attenuator"),
    ]


def _generate(tmp_path: Path, rig, style: str = "production") -> dict[str, Any]:
    config_dir = tmp_path / "config"
    rig.write(config_dir)
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
            measurement_name="mcr_curve",
            selected_resources=_selections(rig),
            params_preset="quick",
            generation_style=style,
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


def test_the_measured_mcr_curve_follows_the_attenuation(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup, measurement = _load(out)
    resources = setup.create_instrument_resources(
        project := load_project_config(Path(out["yaml_file"])),
        resource_source_for(project, Path(out["project_dir"])),
    )

    runner = ProcedureRunner(instruments=resources)
    rows: list[Point] = []
    runner.context.data_bus.subscribe(Point, rows.append)
    assert runner.run(measurement.McrCurveMeasurement(resources).build_procedure()) is Status.SUCCESS

    # One row for the background, then one per attenuation holding both the
    # count and the device voltage read at it.
    background = [r.values for r in rows if r.values["phase"] == "background"]
    counts = [r.values for r in rows if r.values["phase"] == "signal"]
    voltages = counts
    assert len(rows) == 1 + len(ATTENUATIONS)
    assert all({"counts", "device_voltage"} <= row.keys() for row in counts)

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
    assert resources.attenuator.is_shutter_open() is False
    assert resources.attenuator.get_attenuation() == resources.attenuator.get_max_attenuation()


def test_the_generated_mcr_project_runs_as_a_script(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr

    # No workspace manifest above it, so the project records into its own folder.
    with sqlite3.connect(setup_path.parent / "data" / "lab.db") as db:
        (status,) = db.execute("select status from runs").fetchone()
        (points,) = db.execute("select count(*) from points").fetchone()
    assert status == "success"
    assert points == 1 + len(ATTENUATIONS)

    # Read back the way the Data page will: the procedure's own first plot, the
    # background-subtracted rate (a derived column) against attenuation.
    from lab_wizard.lib.data import find, load_plot, to_series
    from lab_wizard.lib.procedures.definition import ProcedureDefinition

    db = setup_path.parent / "data" / "lab.db"
    runs = find(db=db, procedure="mcr_curve")
    (run_id,) = runs.ids
    default = ProcedureDefinition.model_validate(runs.info(run_id)["definition"]).plots[0]
    assert (default.name, default.y) == ("MCR", ["rate_above_dark"])
    (series,) = to_series(load_plot(default.model_copy(update={"runs": [run_id]}), db))
    assert series["x"] == ATTENUATIONS
    assert series["y"] == sorted(series["y"])
    assert series["y"][-1] > 100 * max(series["y"][0], 1.0)

    # The attenuation the attenuator actually reached is recorded beside the one asked for.
    signal = runs.points().filter(pl.col("phase") == "signal")
    assert signal["attenuation_db_reached"].to_list() == pytest.approx(ATTENUATIONS)


def test_an_embedded_mcr_project_runs_outside_any_workspace(tmp_path: Path, rig):
    """The escape hatch's reason to exist: the project folder is all it needs."""
    out = _generate(tmp_path, rig, style="pedagogical_embedded")
    moved = tmp_path / "elsewhere" / Path(out["project_dir"]).name
    shutil.copytree(out["project_dir"], moved)
    setup_path = moved / Path(out["setup_file"]).name

    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(moved)
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
