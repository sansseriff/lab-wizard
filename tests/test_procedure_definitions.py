"""Procedure definitions: parsing, checking, and rendering to Python."""

from __future__ import annotations

import ast
import inspect
from typing import Any

import pytest
from pydantic import ValidationError

from lab_wizard.lib.procedures.catalog import list_step_types, step_catalog, step_params_class
from lab_wizard.lib.procedures.codegen import measurement_module_source, setup_template_source
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.spec import ProcedureError


def _definition(body: dict[str, Any], **overrides: Any) -> ProcedureDefinition:
    data: dict[str, Any] = {
        "name": "probe",
        "roles": {"source": {"behavior": "VSource"}, "counter": {"behavior": "Counter"}},
        "params": {
            "sweep": {"type": "sweep"},
            "gate_s": {"type": "float", "default": 0.5},
            "limit_hz": {"type": "float", "default": 1e6},
        },
        "body": body,
    }
    data.update(overrides)
    return ProcedureDefinition.model_validate(data)


COUNT = {"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "gate_s"}}


def _problems(definition: ProcedureDefinition) -> str:
    with pytest.raises(ProcedureError) as info:
        definition.check()
    return str(info.value)


# --------------------------- the step registry ---------------------------


def test_every_step_schema_can_build_its_runtime_step():
    """A schema field for every required constructor argument, by name — the
    property that lets one generic renderer serve every step. A step that
    breaks it must override render(), as Sweep and Repeat do."""
    types = list_step_types()
    assert {"sequence", "sweep", "count", "set_attenuation", "retry", "if"} <= set(types)
    for type_str in types:
        cls = step_params_class(type_str)
        if "render" in cls.__dict__:
            continue
        runtime = cls.step_class()
        for p in list(inspect.signature(runtime.__init__).parameters.values())[1:]:
            if p.name == "name" or p.kind in (p.VAR_KEYWORD,):
                continue
            if p.default is p.empty or p.kind is p.VAR_POSITIONAL:
                assert p.name in cls.model_fields, f"{cls.__name__} has no field for {runtime.__name__}({p.name})"


def test_the_catalog_describes_fields_for_a_palette():
    catalog = step_catalog()
    count = catalog["count"]
    assert count["fields"]["counter"] == {"kind": "role", "requires": ["Counter"], "required": True, "default": None}
    assert count["fields"]["gate_time"]["kind"] == "value"
    assert count["emits"] == ["counts", "int_time", "count_rate"]
    assert catalog["sweep"]["fields"]["body"]["kind"] == "step"
    assert catalog["sequence"]["fields"]["children"]["kind"] == "steps"
    assert catalog["safe_guard"]["fields"]["instrument"]["requires"] == ["VSource", "Attenuator"]


def test_an_unknown_step_type_names_the_known_ones():
    with pytest.raises(ValidationError, match="Unknown step type 'teleport'"):
        _definition({"type": "teleport"})


# --------------------------- checking ---------------------------


def test_a_correct_definition_checks_and_renders():
    definition = _definition(
        {"type": "sweep", "parameter": "bias", "values": {"param": "sweep"},
         "body": {"type": "sequence", "children": [
             {"type": "set_voltage", "source": {"role": "source"}, "voltage": {"swept": "bias"}}, COUNT]}}
    )
    definition.check()
    expr, _ctx = definition.render_body()
    assert expr == (
        "Sweep('bias', params.sweep.values(), lambda bias: Sequence("
        "SetVoltage(source=source, voltage=bias), Count(counter=counter, gate_time=params.gate_s)))"
    )
    assert definition.emitted_fields() == ["bias", "counts", "int_time", "count_rate"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"type": "count", "counter": {"role": "detector"}, "gate_time": 1.0}, "role 'detector', which the procedure does not declare"),
        ({"type": "count", "counter": {"role": "source"}, "gate_time": 1.0}, "needs a Counter, but role 'source' is a VSource"),
        ({"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "missing"}}, "param 'missing', which is not declared"),
        ({"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "sweep"}}, "reads sweep param 'sweep' as a single value"),
        ({"type": "set_voltage", "source": {"role": "source"}, "voltage": {"swept": "bias"}}, "swept value 'bias' outside any Sweep"),
        ({"type": "sweep", "parameter": "b", "values": {"param": "gate_s"}, "body": COUNT}, "'gate_s', which is not a sweep param"),
        ({"type": "sequence", "children": [COUNT, {"type": "value_above", "field": "temperature", "threshold": 1}]}, "reads 'temperature', which no step in this procedure records"),
    ],
)
def test_every_mistake_is_reported_before_generation(body, expected):
    assert expected in _problems(_definition(body))


def test_all_problems_are_reported_at_once():
    body = {"type": "sequence", "children": [
        {"type": "count", "counter": {"role": "nobody"}, "gate_time": {"param": "nothing"}},
        {"type": "set_voltage", "source": {"role": "source"}, "voltage": {"swept": "x"}},
    ]}
    with pytest.raises(ProcedureError) as info:
        _definition(body).check()
    assert len(info.value.problems) == 3


def test_an_unregistered_behavior_is_reported():
    assert "Unknown behavior 'Teleporter'" in _problems(
        _definition(COUNT, roles={"counter": {"behavior": "Teleporter"}})
    )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"name": "not a name"}, "valid Python identifier"),
        ({"roles": {"params": {"behavior": "VSource"}}}, "reserved"),
        ({"params": {"model_config": {"type": "int"}}}, "collides with a pydantic attribute"),
        ({"params": {"gate_s": {"type": "float", "default": "fast"}}}, "float"),
    ],
)
def test_malformed_definitions_are_rejected_on_load(overrides, match):
    with pytest.raises(ValidationError, match=match):
        _definition(COUNT, **overrides)


# --------------------------- rendering control flow ---------------------------


def test_control_flow_renders_generically():
    definition = _definition(
        {"type": "retry", "max_attempts": 3, "child": {
            "type": "sequence", "children": [COUNT, {
                "type": "if",
                "condition": {"type": "value_above", "field": "count_rate", "threshold": {"param": "limit_hz"}},
                "then": {"type": "set_voltage", "source": {"role": "source"}, "voltage": 0.0},
            }]}}
    )
    definition.check()
    expr, _ = definition.render_body()
    assert expr.startswith("Retry(max_attempts=3, child=Sequence(Count(")
    assert "If(condition=ValueAbove(field='count_rate', threshold=params.limit_hz), then=SetVoltage(" in expr
    assert "otherwise" not in expr


def test_generated_sources_are_valid_python():
    definition = _definition(COUNT)
    ast.parse(measurement_module_source(definition))
    ast.parse(setup_template_source(definition))


def test_nested_sweeps_over_the_same_name_do_not_collide():
    inner = {"type": "sweep", "parameter": "v", "values": [1.0], "body": COUNT}
    expr, _ = _definition({"type": "sweep", "parameter": "v", "values": [0.0], "body": inner}).render_body()
    assert "lambda v: Sweep('v', [1.0], lambda v_2: Count(" in expr
