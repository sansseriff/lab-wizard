"""A generated IV-curve project, run against the simulated SNSPD rack.

This is the full loop the wizard exists to produce, with no hardware and no
mocks in it: a config tree is written, the wizard generates a project folder
from it, the generated ``iv_curve_setup.py`` is executed as written, and the
measurement runs through the real drivers onto the fake rack. What comes back
is checked against the detector model's own physics.

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

from lab_procedure import Observation, ProcedureRunner, RunStarted, Status

from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900Params
from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpibParams
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928Params
from lab_wizard.lib.instruments.fake_rack.modules.fake970 import Fake970Params
from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams
from lab_wizard.lib.measurements.iv_curve.iv_curve import IVCurveMeasurement
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)

PORT = "sim://e2e-rack"
GPIB_ADDRESS = "5"
SOURCE_SLOT = "1"
METER_SLOT = "2"

GPIB_KEY = instrument_hash("fakegpib", PORT)
MAINFRAME_KEY = instrument_hash("fake900", GPIB_ADDRESS)
SOURCE_KEY = instrument_hash("fake928", SOURCE_SLOT)
METER_KEY = instrument_hash("fake970", METER_SLOT)

# The detector under test, and the sweep that walks across its transition.
DEVICE = SnspdModelParams(
    critical_current_a=3.0e-7,
    retrapping_current_a=1.5e-7,
    normal_resistance_ohm=5.0e4,
)
SWEEP_V = [round(0.005 * i, 3) for i in range(13)]  # 0.000 .. 0.060 V
SWITCHING_BIAS_V = 0.03


def _write_config(config_dir: Path) -> None:
    """The config tree a user would build in Manage Instruments."""
    instruments = {
        GPIB_KEY: FakeGpibParams(
            port=PORT,
            children={
                MAINFRAME_KEY: Fake900Params(
                    gpib_address=GPIB_ADDRESS,
                    device=DEVICE,
                    children={
                        SOURCE_KEY: Fake928Params(slot=SOURCE_SLOT),
                        METER_KEY: Fake970Params(slot=METER_SLOT, device_channel=0),
                    },
                )
            },
        )
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


def _generate_project(tmp_path: Path) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_config(config_dir)

    path_to_source = [
        SelectedNodeRef(type="fake928", key=SOURCE_KEY),
        SelectedNodeRef(type="fake900", key=MAINFRAME_KEY),
        SelectedNodeRef(type="fakegpib", key=GPIB_KEY),
    ]
    path_to_meter = [
        SelectedNodeRef(type="fake970", key=METER_KEY),
        SelectedNodeRef(type="fake900", key=MAINFRAME_KEY),
        SelectedNodeRef(type="fakegpib", key=GPIB_KEY),
    ]

    out = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            selected_resources=[
                SelectedResource(
                    variable_name="voltage_source",
                    type="fake928",
                    key=SOURCE_KEY,
                    path=path_to_source,
                ),
                SelectedResource(
                    variable_name="voltage_sense",
                    type="fake970",
                    key=METER_KEY,
                    channel_index=0,
                    path=path_to_meter,
                ),
            ],
            project_prefix="iv_fake",
        ),
    )
    _set_measurement_params(Path(out["yaml_file"]))
    return out


def _set_measurement_params(yaml_path: Path) -> None:
    """Edit the generated project YAML the way a user tunes a run.

    The model's hardcoded resistor matches the IV measurement default. This
    edit only chooses a short, fast sweep for the test.
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


def _load_setup_module(setup_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("generated_iv_setup", setup_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def test_generated_setup_wires_the_simulated_rack(tmp_path: Path) -> None:
    out = _generate_project(tmp_path)
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    measurement_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    ast.parse(measurement_text)

    assert "from iv_curve import IVCurveMeasurement" in setup_text
    assert "class IVCurveMeasurement" in measurement_text

    assert (
        "from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpib"
        in setup_text
    )
    assert "from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900" in setup_text
    assert (
        "from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928"
        in setup_text
    )
    assert f"FakeGpib.from_config(resources, key={GPIB_KEY!r})" in setup_text
    assert "Fake900.from_config(" in setup_text
    assert ".channels[0]" in setup_text

    payload = cast(
        dict[str, Any],
        YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8")),
    )
    rack = payload["resources"]["instruments"][GPIB_KEY]
    assert rack["type"] == "fakegpib"
    mainframe = rack["children"][MAINFRAME_KEY]
    # The detector's constants travel with the project, so a simulated run is
    # reproducible from the YAML alone.
    assert mainframe["device"]["critical_current_a"] == DEVICE.critical_current_a
    assert set(mainframe["children"]) == {SOURCE_KEY, METER_KEY}
    assert payload["measurement"]["params"]["readout"]["bias_resistance_ohm"] == 100_000.0


def test_generated_project_measures_the_simulated_iv_curve(tmp_path: Path) -> None:
    out = _generate_project(tmp_path)
    module = _load_setup_module(Path(out["setup_file"]))
    project = load_project_config(Path(out["yaml_file"]))
    resources = module.create_instrument_resources(project)

    measurement = IVCurveMeasurement(resources)
    runner = ProcedureRunner(instruments=resources)
    observations: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, observations.append)

    status = runner.run(
        measurement.build_procedure(),
        RunStarted(run_type="iv_curve", config=resources.params.model_dump(mode="json")),
    )
    assert status is Status.SUCCESS
    assert len(observations) == len(SWEEP_V)

    expected = _expected_curve()
    for observation, bias, (true_current, true_voltage) in zip(
        observations, SWEEP_V, expected
    ):
        data = observation.data
        assert data["bias_voltage"] == pytest.approx(bias)
        assert data["sense_voltage"] == pytest.approx(true_voltage)
        assert data["current"] == pytest.approx(true_current)

    sense = [o.data["sense_voltage"] for o in observations]
    superconducting = [
        v for bias, v in zip(SWEEP_V, sense) if bias < SWITCHING_BIAS_V
    ]
    resistive = [v for bias, v in zip(SWEEP_V, sense) if bias >= SWITCHING_BIAS_V]

    assert set(superconducting) == {0.0}, "no voltage before the detector switches"
    assert all(v > 0 for v in resistive), "a switched detector drops voltage"
    assert all(a < b for a, b in zip(resistive, resistive[1:])), "monotonic above"


def test_measured_current_matches_the_detectors_true_bias_current(
    tmp_path: Path,
) -> None:
    """The point of the whole exercise: the numbers mean what they claim.

    The measurement never sees a current — it infers one from the sensed
    voltage and the bias resistance in the project YAML. Comparing that against
    the current the model actually pushed is the check that the inference is
    right, and it is only possible because the detector is simulated.
    """
    out = _generate_project(tmp_path)
    module = _load_setup_module(Path(out["setup_file"]))
    project = load_project_config(Path(out["yaml_file"]))
    resources = module.create_instrument_resources(project)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, observations.append)
    runner.run(IVCurveMeasurement(resources).build_procedure())

    switching = next(
        o for o in observations if o.data["bias_voltage"] >= SWITCHING_BIAS_V
    )
    assert switching.data["current"] == pytest.approx(
        DEVICE.critical_current_a / 1.5, rel=1e-6
    )


def test_generated_setup_runs_as_a_script(tmp_path: Path) -> None:
    """The generated file is meant to be run, not only imported."""
    out = _generate_project(tmp_path)
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
