"""A generated IV-curve project, run against the simulated SNSPD bench.

This is the full loop the wizard exists to produce, with no hardware and no
mocks in it: a config tree is written, the wizard generates a project folder
from it, the generated ``iv_curve_setup.py`` is executed as written, and the
measurement runs through the real drivers onto lab_sim's simulated rack. What
comes back is checked against the detector model's own physics.

Two ways of running the generated file are covered, because they fail
differently: importing it and driving the procedure directly (so the data
stream can be inspected), and running the file as a script the way a user
would (so the ``__main__`` block, project resolution, and preflight are
exercised too).
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from ruamel.yaml import YAML

import polars as pl

from lab_procedure import Status

from lab_sim import SnspdModel, SnspdParams
from lab_wizard.lib.data import find
from lab_wizard.lib.utilities.config_io import load_instruments
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.lib.client.project_resources import resource_source_for
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest

# The detector under test, and the sweep that walks across its transition.
DEVICE = SnspdParams(
    critical_current_a=3.0e-7,
    retrapping_current_a=1.5e-7,
    normal_resistance_ohm=5.0e4,
)
SWEEP_V = [round(0.005 * i, 3) for i in range(13)]  # 0.000 .. 0.060 V
SWITCHING_BIAS_V = 0.03


def _generate_project(tmp_path: Path, rig) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    rig.write(config_dir, ("source", "meter"))
    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=tmp_path / "projects",
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            kind="procedure",
            selected_resources=[rig.select("voltage_source", "source"), rig.select("voltage_sense", "meter")],
            project_prefix="iv_sim",
        ),
    )
    _set_measurement_params(Path(out["yaml_file"]))
    return out


def _set_measurement_params(yaml_path: Path) -> None:
    """Edit the generated project YAML the way a user tunes a run.

    The bench's bias resistor matches the IV measurement default. This edit
    only chooses a short, fast sweep for the test.
    """
    yaml = YAML(typ="rt")
    io: Any = yaml
    with yaml_path.open("r", encoding="utf-8") as handle:
        payload = io.load(handle)

    params = payload["measurement"]["params"]
    params["bias"]["sweep"] = {"mode": "explicit", "values_V": list(SWEEP_V)}
    params["bias"]["settle_s"] = 0.0

    with yaml_path.open("w", encoding="utf-8") as handle:
        io.dump(payload, handle)


def _load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_and_read_back(out: dict[str, Any]) -> pl.DataFrame:
    """Run the generated project once and read its run back, derived columns included.

    The current is not recorded: it is the procedure's derived column, computed
    from the sensed voltage and the bias resistance the run was given.
    """
    setup = _load_module(Path(out["setup_file"]), "generated_iv_setup")
    measurement = _load_module(Path(out["measurement_file"]), "generated_iv_curve")
    project = load_project_config(Path(out["yaml_file"]))
    resources = setup.create_instrument_resources(
        project, resource_source_for(project, Path(out["project_dir"]))
    )
    assert measurement.IvCurveMeasurement(resources).run_measurement() is Status.SUCCESS

    runs = find(db=Path(out["project_dir"]) / "data" / "lab.db", procedure="iv_curve")
    assert len(runs) == 1
    return runs.points(runs.derived())


def _expected_curve() -> list[tuple[float, float]]:
    """What the detector model says the sweep should produce."""
    model = SnspdModel(DEVICE)
    model.set_output_enabled(True)
    out: list[tuple[float, float]] = []
    for bias in SWEEP_V:
        # The SIM928 driver formats to millivolts, so that is what the
        # detector actually sees.
        model.set_bias_voltage(float(f"{bias:0.3f}"))
        current, voltage = model.solve()
        out.append((current, voltage))
    return out


# ---------------------------------------------------------------------------


def test_generated_setup_wires_the_simulated_rack(tmp_path: Path, rig) -> None:
    out = _generate_project(tmp_path, rig)
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    measurement_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    ast.parse(measurement_text)

    assert "from iv_curve import IvCurveMeasurement" in setup_text
    assert "class IvCurveMeasurement" in measurement_text

    config = load_instruments(tmp_path / "config")
    mainframe_params = config[rig.gpib].children[rig.mainframe]
    source_name = mainframe_params.children[rig.source].attribute_name
    meter_name = mainframe_params.children[rig.meter].channels[0].attribute_name
    assert source_name and meter_name

    # Instruments are referenced by name, never by a hash of their address.
    assert f"resources.from_attribute({source_name!r})" in setup_text
    assert f"resources.from_attribute({meter_name!r})" in setup_text
    assert ".from_config(resources, key=" not in setup_text

    payload = cast(
        dict[str, Any],
        YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8")),
    )
    # No copy of the rack: its params stay in the workspace config, and the
    # project says which instruments it uses.
    assert "instruments" not in payload["resources"]
    assert payload["resources"]["instrument_sources"] == {source_name: "local", meter_name: "local"}
    assert payload["measurement"]["params"]["readout"]["bias_resistance_ohm"] == 100_000.0


def test_generated_project_measures_the_simulated_iv_curve(tmp_path: Path, rig) -> None:
    rows = _run_and_read_back(_generate_project(tmp_path, rig))
    assert rows.height == len(SWEEP_V)

    expected = _expected_curve()
    for row, bias, (true_current, true_voltage) in zip(rows.iter_rows(named=True), SWEEP_V, expected):
        assert row["bias_voltage"] == pytest.approx(bias)
        assert row["sense_voltage"] == pytest.approx(true_voltage)
        assert row["current"] == pytest.approx(true_current)

    sense = rows["sense_voltage"].to_list()
    superconducting = [
        v for bias, v in zip(SWEEP_V, sense) if bias < SWITCHING_BIAS_V
    ]
    resistive = [v for bias, v in zip(SWEEP_V, sense) if bias >= SWITCHING_BIAS_V]

    assert set(superconducting) == {0.0}, "no voltage before the detector switches"
    assert all(v > 0 for v in resistive), "a switched detector drops voltage"
    assert all(a < b for a, b in zip(resistive, resistive[1:])), "monotonic above"


def test_measured_current_matches_the_detectors_true_bias_current(tmp_path: Path, rig) -> None:
    """The point of the whole exercise: the numbers mean what they claim.

    The measurement never sees a current — it is inferred, when the run is
    read, from the sensed voltage and the bias resistance in the run's params.
    Comparing that against the current the model actually pushed is the check
    that the inference is right, and it is only possible because the detector
    is simulated.
    """
    rows = _run_and_read_back(_generate_project(tmp_path, rig))
    switching = rows.filter(pl.col("bias_voltage") >= SWITCHING_BIAS_V).row(0, named=True)
    assert switching["current"] == pytest.approx(DEVICE.critical_current_a / 1.5, rel=1e-6)


def test_generated_setup_runs_as_a_script(tmp_path: Path, rig) -> None:
    """The generated file is meant to be run, not only imported."""
    out = _generate_project(tmp_path, rig)
    setup_path = Path(out["setup_file"])

    result = subprocess.run(
        [sys.executable, str(setup_path)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(setup_path.parent),
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
