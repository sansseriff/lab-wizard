"""What a procedure says about its data: plots, derived columns, units, read-backs.

And the checks that stop a definition losing data silently: a parameter bound
twice, a reading recorded under a bound parameter's name, or a derived column
or plot that names something the procedure never records.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from lab_procedure import Point, ProcedureRunner, Status, Sweep, step_catalog

from lab_wizard.lib.data import ExpressionError, compile_expression, derive
from lab_wizard.lib.instruments.general.attenuator import StandInAttenuator
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.task_adapters.instrument_steps import SetAttenuation
from lab_wizard.wizard.backend.procedures_api import check_definition

COUNT = {"type": "count", "counter": {"role": "counter"}, "gate_time": {"param": "gate_s"}}
VOLTS = {"type": "read_voltage", "sense": {"role": "sense"}, "field": "device_voltage"}


def _definition(body: dict[str, Any], **extra: Any) -> ProcedureDefinition:
    return ProcedureDefinition.model_validate({
        "name": "probe",
        "roles": {
            "counter": {"behavior": "Counter"},
            "sense": {"behavior": "VSense"},
            "attenuator": {"behavior": "Attenuator"},
        },
        "params": {
            "sweep": {"type": "sweep", "unit": "V"},
            "gate_s": {"type": "float", "default": 0.5},
            "resistance": {"type": "float", "default": 1000.0, "unit": "ohm"},
            "label": {"type": "str", "default": "x"},
        },
        "body": body,
        **extra,
    })


def _sweep(body: dict[str, Any], parameter: str = "bias") -> dict[str, Any]:
    return {"type": "sweep", "parameter": parameter, "values": {"param": "sweep"}, "body": body}


def _messages(located: list[tuple[Any, str]]) -> str:
    return "\n".join(message for _path, message in located)


# --------------------------- units ---------------------------


def test_columns_carry_the_units_their_steps_and_params_declare():
    definition = _definition(_sweep({"type": "sequence", "children": [
        {"type": "set_attenuation", "attenuator": {"role": "attenuator"}, "attenuation_db": 3.0,
         "record": "attenuation_db_reached"},
        COUNT,
        VOLTS,
    ]}))
    assert definition.columns() == {
        "bias": {"unit": "V"},
        "attenuation_db_reached": {"unit": "dB"},
        "counts": {"unit": None},
        "int_time": {"unit": "s"},
        "count_rate": {"unit": "Hz"},
        "device_voltage": {"unit": "V"},
    }


def test_the_catalog_offers_record_as_a_column_a_setter_writes():
    for step in ("set_attenuation", "set_threshold", "set_laser_power"):
        field = step_catalog()[step]["fields"]["record"]
        assert field["column"] == "records" and field["optional"]


# --------------------------- reading back what a setter reached ---------------------------


def test_a_setter_records_what_the_instrument_reached_beside_what_was_asked(rig):
    attenuator = rig.yoko_params().create_inst().make_child(rig.attenuator)
    rows: list[Point] = []
    runner = ProcedureRunner()
    runner.context.data_bus.subscribe(Point, rows.append)
    runner.run(Sweep("attenuation_db", [12.34567, 99.0],
                     lambda a: SetAttenuation(attenuator, a, record="attenuation_db_reached")))

    # The hardware quantizes, and clamps at its maximum.
    assert [r.values for r in rows] == [
        {"attenuation_db": 12.34567, "attenuation_db_reached": 12.346},
        {"attenuation_db": 99.0, "attenuation_db_reached": attenuator.get_max_attenuation()},
    ]


def test_a_setter_without_record_records_nothing():
    attenuator = StandInAttenuator()
    rows: list[Point] = []
    runner = ProcedureRunner()
    runner.context.data_bus.subscribe(Point, rows.append)
    assert runner.run(SetAttenuation(attenuator, 3.0)) is Status.SUCCESS
    assert rows == []


# --------------------------- checks that stop silent data loss ---------------------------


def test_a_nested_sweep_reusing_a_name_is_an_error():
    definition = _definition(_sweep(_sweep(COUNT, "bias")))
    assert "binds 'bias', which an enclosing step already binds" in _messages(definition.diagnose())


def test_recording_a_bound_parameters_name_is_an_error():
    definition = _definition(_sweep({**VOLTS, "field": "bias"}))
    assert "records 'bias', which is a parameter bound by an enclosing step" in _messages(definition.diagnose())


def test_two_steps_recording_one_field_in_a_loop_body_is_a_warning():
    definition = _definition(_sweep({"type": "sequence", "children": [COUNT, COUNT]}))
    definition.check()  # it still generates and runs
    assert "2 steps record 'counts' at the same parameter values" in _messages(definition.warnings())


def test_alternatives_that_record_the_same_field_are_not_warned_about():
    branches = {"type": "if", "condition": {"type": "value_above", "field": "counts", "threshold": 1},
                "then": COUNT, "otherwise": COUNT}
    definition = _definition({"type": "sequence", "children": [COUNT, _sweep(branches)]})
    assert definition.warnings() == []


def test_separate_loops_recording_the_same_field_are_fine():
    definition = _definition({"type": "sequence", "children": [_sweep(COUNT, "a"), _sweep(COUNT, "b")]})
    assert definition.warnings() == []


# --------------------------- derived columns ---------------------------


def test_derived_columns_are_checked_against_what_the_procedure_records():
    ok = _definition(_sweep({"type": "sequence", "children": [COUNT, VOLTS]}), derived={
        "current": '(bias - device_voltage) / param("resistance")',
        "normalized": "current / max(current)",
    })
    ok.check()

    for derived, complaint in [
        ({"x": "voltage * 2"}, "no column named 'voltage'"),
        ({"x": "y + 1", "y": "x + 1"}, "is part of a cycle"),
        ({"counts": "count_rate"}, "would replace a recorded column"),
        ({"x": 'counts / param("missing")'}, "param('missing'), which is not declared"),
        ({"x": 'counts / param("label")'}, "which is a str, not a number"),
        ({"x": "counts.real"}, "not part of the expression language"),
        ({"2x": "counts"}, "must be a name"),
    ]:
        definition = _definition(COUNT, derived=derived)
        assert complaint in _messages(definition.diagnose()), derived


def test_param_reads_each_runs_own_value():
    frame = pl.DataFrame({"run_id": [1, 1, 2], "seq": [0, 1, 0], "v": [2.0, 4.0, 2.0]})
    params = {1: {"readout": {"r": 2.0}}, 2: {"readout": {"r": 10.0}}}
    out = derive(frame, {"i": 'v / param("readout.r")'}, params)
    assert out["i"].to_list() == [1.0, 2.0, 0.2]


def test_param_needs_params_and_a_number():
    with pytest.raises(ExpressionError, match="needs the runs' params"):
        compile_expression('param("r")', ["run_id"])
    with pytest.raises(ExpressionError, match="only a number can be used"):
        compile_expression('param("mode")', ["run_id"], {1: {"mode": "linear"}})
    with pytest.raises(ExpressionError, match="one quoted param path"):
        compile_expression("param(r)", ["run_id", "r"], {1: {}})


# --------------------------- plots ---------------------------


def test_plots_are_checked_against_recorded_and_derived_columns():
    body = _sweep({"type": "sequence", "children": [COUNT, VOLTS]})
    ok = _definition(body, derived={"above": "counts - min(counts)"}, plots=[
        {"name": "counts", "x": "bias", "y": ["above"], "y2": ["device_voltage"], "series": "run"},
        {"name": "per bias", "x": "device_voltage", "y": ["count_rate"], "series": "bias",
         "where": {"bias": {"per_run": "min"}}},
    ])
    ok.check()
    assert [p.name for p in ok.plots] == ["counts", "per bias"]

    wrong = _definition(body, plots=[
        {"x": "bias", "y": ["voltage"]},
        {"x": "bias", "y": ["counts"], "runs": [3]},
        {"x": "bias", "y": ["counts"], "where": {"phase": "signal"}},
        {"x": "bias", "y": ["counts"], "series": "trigger"},
    ])
    messages = _messages(wrong.diagnose())
    assert "no column named 'voltage'" in messages
    assert "names no runs" in messages
    assert "no column named 'phase'" in messages
    assert "no column named 'trigger'" in messages


def test_the_composer_check_reports_warnings_beside_problems():
    definition = _definition(_sweep({"type": "sequence", "children": [COUNT, COUNT]}))
    result = check_definition(definition.model_dump(mode="json"))
    assert result["ok"] is True
    assert result["warnings"] and result["warnings"][0]["path"] == ["body"]
    assert "counts" in result["warnings"][0]["message"]


def test_plots_and_derived_survive_a_round_trip_through_the_generated_module(tmp_path: Path):
    from lab_wizard.lib.procedures.codegen import definition_block

    definition = _definition(COUNT, derived={"r": "counts * 2"}, plots=[{"x": "counts", "y": ["r"]}])
    namespace: dict[str, Any] = {"Any": Any}
    exec(definition_block(definition), namespace)
    assert ProcedureDefinition.model_validate(namespace["DEFINITION"]) == definition
