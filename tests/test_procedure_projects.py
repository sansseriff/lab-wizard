"""A composed procedure, from YAML definition to a project that measures.

The proof for procedure plan Phases 1-3: a PCR sweep written as a procedure
definition — no Python — generates a project that runs against the simulated
bench and measures the detector model's curve, exactly as the hand-written
``pcr_curve`` does. And none of it required a line of generator code specific
to this procedure.
"""

from __future__ import annotations

import importlib
import json
import importlib.util
import math
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from lab_procedure import Point, ProcedureRunner, Status

from lab_wizard.sim import SnspdModel, SnspdParams
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import list_presets, load_procedure, save_preset, save_procedure
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    save_instruments_to_config,
)
from lab_wizard.lib.project import Project
from lab_wizard.lib.procedures.codegen import built_from, procedure_hash
from lab_wizard.lib.data import find
from lab_wizard.wizard.backend import data_api
from lab_wizard.wizard.backend.procedure_generation import (
    generate_procedure_project,
    update_project_procedure,
)
from lab_wizard.wizard.backend.regenerate import RegenerateError, regenerate_project
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
    """The generated setup module, and the measurement module it loaded beside it."""
    spec = importlib.util.spec_from_file_location("composed_setup", Path(out["setup_file"]))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, module.measurement


def _resources(setup: Any, out: dict[str, Any]) -> Any:
    """Each role's instrument from the project YAML, as the setup's __main__ builds them."""
    return Project.load(Path(out["project_dir"])).resources(setup.Resources)


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
    assert "def build_composed_pcr_procedure(resources: ComposedPcrResources)" in module_text
    # The params types sit with the step tree; their values only in the YAML.
    assert "class ComposedPcrParams(BaseModel):" in module_text
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "class Resources(measurement.ComposedPcrResources):" in setup_text
    assert "BaseModel" not in setup_text and "from_attribute" not in setup_text
    assert all(isinstance(binding, str) for binding in payload["roles"].values())  # all local


def test_the_composed_procedure_measures_the_detectors_curve(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup, measurement = _load_setup(out)
    resources = _resources(setup, out)

    runner = ProcedureRunner(instruments=resources)
    rows: list[Point] = []
    runner.context.data_bus.subscribe(Point, rows.append)
    status = runner.run(measurement.build_composed_pcr_procedure(resources))

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
    """The measurement module declares the params as source; storage validates with a
    model built at run time. They must agree, or a preset could pass one and fail the other."""
    out = _generate(tmp_path, rig)
    _setup, measurement = _load_setup(out)
    defaults = ProcedureDefinition.model_validate(PCR_DEFINITION).param_defaults()
    assert measurement.ComposedPcrParams.model_validate(defaults).model_dump(mode="json") == defaults
    # No value comes from the classes: the YAML is the only place one lives.
    with pytest.raises(ValueError):
        measurement.ComposedPcrParams()


def _with_retry(definition: dict[str, Any]) -> dict[str, Any]:
    """The same procedure, with each count retried up to three times."""
    changed = deepcopy(definition)
    point = changed["body"]["children"][1]["body"]["body"]["children"]
    point[2] = {"type": "retry", "max_attempts": 3, "child": point[2]}
    return changed


def _edit_yaml(path: Path, edit: Any) -> None:
    rt = YAML(typ="rt")
    document = rt.load(path.read_text(encoding="utf-8"))
    edit(document)
    with path.open("w", encoding="utf-8") as handle:
        rt.dump(document, handle)


def test_the_project_yaml_carries_its_procedure_and_the_module_is_built_from_it(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    payload = YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8"))
    expected = ProcedureDefinition.model_validate(PCR_DEFINITION).model_dump(mode="json", exclude_none=True)
    assert payload["procedure"] == expected
    assert list(payload) == ["project", "run", "setup", "roles", "procedure", "measurement", "outputs"]

    module_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    assert "DEFINITION" not in module_text  # the YAML is the one copy
    assert built_from(module_text) == procedure_hash(payload["procedure"])


def test_editing_the_procedure_block_rebuilds_the_module_before_the_next_run(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    yaml_path, module_path = Path(out["yaml_file"]), Path(out["measurement_file"])

    def edit(doc: Any) -> None:
        procedure = _with_retry(doc["procedure"])
        # A plot and a derived column added in the project's YAML alone.
        procedure["derived"] = {"rate_hz": "counts / int_time"}
        procedure["plots"] = [{"name": "Rate", "x": "bias_voltage", "y": ["rate_hz"]}]
        doc["procedure"] = procedure

    _edit_yaml(yaml_path, edit)

    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode == 0, result.stderr
    assert "built composed_pcr_measurement.py again" in result.stdout
    assert "Retry(" in module_path.read_text(encoding="utf-8")

    # The run records the very block its code was built from.
    runs = find(db=Path(out["project_dir"]) / "data" / "lab.db")
    recorded = runs.info(runs.ids[0])["definition"]
    assert recorded == YAML(typ="safe").load(yaml_path.read_text(encoding="utf-8"))["procedure"]
    assert '"type": "retry"' in json.dumps(recorded)

    # The Data page draws the run by what it recorded: the new plot, and its derived column.
    db = Path(out["project_dir"]) / "data" / "lab.db"
    detail = data_api.run_detail(db, tmp_path / "config", runs.ids[0])
    assert [p["name"] for p in detail["plots"]] == ["Rate"]
    drawn = data_api.plot(db, detail["plots"][0])
    (series,) = drawn["series"]
    assert series["x"] == SWEEP_V and len(series["y"]) == len(SWEEP_V)


def test_a_procedure_block_that_does_not_check_stops_the_run_before_anything_opens(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    _edit_yaml(Path(out["yaml_file"]), lambda doc: doc["procedure"]["body"].update(type="no_such_step"))
    setup_path = Path(out["setup_file"])
    result = subprocess.run(
        [sys.executable, str(setup_path)], capture_output=True, text=True, timeout=300, cwd=str(setup_path.parent)
    )
    assert result.returncode != 0
    assert "procedure: block" in result.stderr and "no_such_step" in result.stderr


def test_updating_from_the_procedure_replaces_only_the_procedure_block(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    yaml_path = Path(out["yaml_file"])
    before = YAML(typ="safe").load(yaml_path.read_text(encoding="utf-8"))

    save_procedure(tmp_path / "config", ProcedureDefinition.model_validate(_with_retry(PCR_DEFINITION)))
    update_project_procedure(tmp_path / "config", Path(out["project_dir"]))

    after = YAML(typ="safe").load(yaml_path.read_text(encoding="utf-8"))
    assert '"type": "retry"' in json.dumps(after["procedure"])
    assert {k: v for k, v in after.items() if k != "procedure"} == {k: v for k, v in before.items() if k != "procedure"}
    assert "Retry(" in Path(out["measurement_file"]).read_text(encoding="utf-8")

    setup, measurement = _load_setup(out)
    resources = _resources(setup, out)
    assert ProcedureRunner().run(measurement.build_composed_pcr_procedure(resources)) is Status.SUCCESS


def test_wizard_regenerate_writes_the_setups_roles_from_the_yaml(tmp_path: Path, rig):
    out = _generate(tmp_path, rig)
    setup_path = Path(out["setup_file"])
    generated = setup_path.read_text(encoding="utf-8")
    # A setup whose roles block has gone stale: emptied, as if the roles changed.
    stale = generated.replace("    voltage_source: Sim928\n", "").replace("    counter: Keysight53220AChannel\n", "")
    setup_path.write_text(stale, encoding="utf-8")

    done = regenerate_project(Path(out["project_dir"]))
    assert any("roles" in line for line in done)
    text = setup_path.read_text(encoding="utf-8")
    assert "    voltage_source: Sim928\n" in text and "    counter: Keysight53220AChannel\n" in text

    # A role the procedure has but roles: does not bind is named.
    _edit_yaml(Path(out["yaml_file"]), lambda doc: doc["roles"].pop("counter"))
    with pytest.raises(RegenerateError, match="counter"):
        regenerate_project(Path(out["project_dir"]))


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
