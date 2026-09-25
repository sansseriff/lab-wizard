"""A composed procedure, from YAML definition to a project that measures.

The proof for procedure plan Phases 1-3: a PCR sweep written as a procedure
definition — no Python — generates a project that runs against the simulated
bench and measures the detector model's curve, exactly as the hand-written
``pcr_curve`` does. And none of it required a line of generator code specific
to this procedure.
"""

from __future__ import annotations

import importlib
import importlib.util
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from lab_procedure import Point, ProcedureRunner, Status

from lab_sim import SnspdModel, SnspdParams
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import list_presets, load_procedure, save_preset, save_procedure
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.lib.client.project_resources import resource_source_for
from lab_wizard.wizard.backend.procedure_generation import (
    generate_procedure_project,
    refresh_procedure_source,
)
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest, SelectedResource

DEVICE = SnspdParams()
SWEEP_V = [0.0, 0.012, 0.02, 0.024, 0.028]
GATE_S = 0.05
THRESHOLD_MV = -50.0

# The hand-written pcr_curve, as a definition: set the threshold, then sweep the
# bias with the source guarded, counting at every point.
PCR_DEFINITION: dict[str, Any] = {
    "name": "composed_pcr",
    "description": "Count rate against bias voltage, composed from generic steps.",
    "roles": {"voltage_source": {"behavior": "VSource"}, "counter": {"behavior": "Counter"}},
    "params": {
        "bias": {
            "sweep": {"type": "sweep", "default": {"mode": "linear", "start": 0.0, "stop": 0.03, "step": 0.002}},
            "settle_s": {"type": "float", "default": 0.05, "unit": "s"},
        },
        "readout": {
            "gate_time_s": {"type": "float", "default": 1.0, "unit": "s"},
            "threshold_mV": {"type": "float", "default": -50.0, "unit": "mV"},
        },
    },
    "body": {
        "type": "sequence",
        "children": [
            {"type": "set_threshold", "counter": {"role": "counter"}, "threshold_mV": {"param": "readout.threshold_mV"}},
            {
                "type": "source_guard",
                "source": {"role": "voltage_source"},
                "body": {
                    "type": "sweep",
                    "parameter": "bias_voltage",
                    "values": {"param": "bias.sweep"},
                    "body": {
                        "type": "sequence",
                        "children": [
                            {"type": "set_voltage", "source": {"role": "voltage_source"}, "voltage": {"swept": "bias_voltage"}},
                            {"type": "wait", "seconds": {"param": "bias.settle_s"}},
                            {"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "readout.gate_time_s"}},
                        ],
                    },
                },
            },
        ],
    },
}


def _write_instruments(config_dir: Path, rig) -> None:
    counter = rig.counter_params()
    counter.channels = {0: Keysight53220AChannelParams(threshold_mV=400.0)}  # wrong on purpose
    instruments = {**rig.instruments(("source",)), rig.counter: counter}
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


def _selections(rig) -> list[SelectedResource]:
    return [rig.select("voltage_source", "source"), rig.select("counter", "counter")]


def _generate(tmp_path: Path, rig, preset: str | None = "bench_sweep") -> dict[str, Any]:
    config_dir, projects_dir = tmp_path / "config", tmp_path / "projects"
    _write_instruments(config_dir, rig)
    definition = ProcedureDefinition.model_validate(PCR_DEFINITION)
    save_procedure(config_dir, definition)
    save_preset(
        config_dir,
        "composed_pcr",
        "bench_sweep",
        {
            "bias": {"sweep": {"mode": "explicit", "values_V": SWEEP_V}, "settle_s": 0.0},
            "readout": {"gate_time_s": GATE_S, "threshold_mV": THRESHOLD_MV},
        },
        definition.params_model(),
    )
    return generate_procedure_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="composed_pcr",
            selected_resources=_selections(rig),
            project_prefix="composed",
            params_preset=preset,
        ),
    )


def _load_setup(out: dict[str, Any]) -> Any:
    setup_path = Path(out["setup_file"])
    sys.path.insert(0, str(setup_path.parent))  # the measurement module is its sibling
    try:
        spec = importlib.util.spec_from_file_location("composed_setup", setup_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        measurement = importlib.import_module("composed_pcr")
        return module, measurement
    finally:
        sys.path.remove(str(setup_path.parent))
        sys.modules.pop("composed_pcr", None)


def _expected_counts(bias_v: float) -> float:
    model = SnspdModel(DEVICE)
    model.set_output_enabled(True)
    model.set_bias_voltage(float(f"{bias_v:0.3f}"))
    return model.count_rate(THRESHOLD_MV) * GATE_S


def test_the_generated_project_carries_the_preset_and_the_procedure(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    payload = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["project"]["measurement_type"] == "composed_pcr"
    assert payload["measurement"]["params"]["bias"]["sweep"] == {"mode": "explicit", "values": SWEEP_V}

    module_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    assert "def build_composed_pcr_procedure(" in module_text
    assert "# wizard:procedure:start" in module_text and "# wizard:procedure:end" in module_text
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "class ComposedPcrParams(BaseModel):" in setup_text
    assert "resources.from_attribute(" in setup_text
    assert ".from_config(resources, key=" not in setup_text
    assert "instruments" not in payload["resources"]
    assert set(payload["resources"]["instrument_sources"].values()) == {"local"}


def test_the_composed_procedure_measures_the_detectors_curve(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup, measurement = _load_setup(out)
    project = load_project_config(Path(out["yaml_file"]))
    resources = setup.create_instrument_resources(
        project, resource_source_for(project, Path(out["project_dir"]))
    )

    runner = ProcedureRunner(instruments=resources)
    rows: list[Point] = []
    runner.context.data_bus.subscribe(Point, rows.append)
    status = runner.run(measurement.ComposedPcrMeasurement(resources).build_procedure())

    assert status is Status.SUCCESS
    assert [row.values["bias_voltage"] for row in rows] == SWEEP_V
    for row, bias in zip(rows, SWEEP_V):
        expected = _expected_counts(bias)
        assert abs(row.values["counts"] - expected) <= 5 * math.sqrt(expected) + 5, (bias, row.values)
    # The counter's config said 400 mV, which counts nothing; the procedure set its own.
    assert rows[-1].values["counts"] > 1000
    assert resources.voltage_source is not None


def test_the_generated_setup_runs_as_a_script(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr


def test_without_a_preset_the_definitions_defaults_are_used(tmp_path: Path, rig):
    out = _generate(tmp_path, rig, preset=None)
    payload = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["measurement"]["params"]["readout"] == {"gate_time_s": 1.0, "threshold_mV": -50.0}


def test_the_generated_params_model_matches_the_definitions(tmp_path: Path, rig):
    """The setup file declares the params as source; storage validates with a model
    built at run time. They must agree, or a preset could pass one and fail the other."""
    out = _generate(tmp_path, rig)
    setup, _measurement = _load_setup(out)
    definition = ProcedureDefinition.model_validate(PCR_DEFINITION)
    assert setup.ComposedPcrParams().model_dump(mode="json") == definition.param_defaults()


def test_refreshing_replaces_the_tree_and_keeps_edits_outside_it(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    project_dir = Path(out["project_dir"])
    module_path = Path(out["measurement_file"])
    module_path.write_text(
        module_path.read_text(encoding="utf-8") + "\n\ndef my_analysis():\n    return 'kept'\n",
        encoding="utf-8",
    )

    # Change the definition: retry each count up to three times.
    changed = dict(PCR_DEFINITION)
    changed["body"] = deepcopy(PCR_DEFINITION["body"])
    point = changed["body"]["children"][1]["body"]["body"]["children"]
    point[2] = {"type": "retry", "max_attempts": 3, "child": point[2]}
    save_procedure(tmp_path / "config", ProcedureDefinition.model_validate(changed))

    refresh_procedure_source(tmp_path / "config", project_dir)
    text = module_path.read_text(encoding="utf-8")
    assert "Retry(" in text
    assert "from lab_procedure.steps import" in text and "Retry" in text
    assert "def my_analysis():" in text

    setup, measurement = _load_setup(out)
    resources = setup.create_instrument_resources(
        project := load_project_config(Path(out["yaml_file"])),
        resource_source_for(project, Path(out["project_dir"])),
    )
    assert ProcedureRunner().run(measurement.ComposedPcrMeasurement(resources).build_procedure()) is Status.SUCCESS
    # The definition every run records was regenerated with the tree.
    assert ProcedureDefinition.model_validate(measurement.DEFINITION) == ProcedureDefinition.model_validate(changed)


def test_the_built_in_pcr_curve_takes_a_preset(tmp_path: Path, rig):
    config_dir = tmp_path / "config"
    _write_instruments(config_dir, rig)
    model = load_procedure(config_dir, "pcr_curve").params_model()
    save_preset(config_dir, "pcr_curve", "short", {"readout": {"gate_time_s": 0.25}}, model)
    assert list_presets(config_dir, "pcr_curve") == ["short"]

    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=tmp_path / "projects",
        req=GenerateProjectRequest(
            measurement_name="pcr_curve", kind="procedure", selected_resources=_selections(rig), params_preset="short"
        ),
    )
    payload = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    assert payload["measurement"]["params"]["readout"]["gate_time_s"] == 0.25
    assert payload["measurement"]["params"]["bias"]["settle_s"] == 0.05  # untouched default
