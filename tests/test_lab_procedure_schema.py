"""lab_procedure's step schemas and definitions, on their own and building step trees."""

from __future__ import annotations

import importlib
import subprocess
import sys
from typing import Any, Literal

import pytest
from pydantic import Field

from lab_procedure import (
    Point,
    ProcedureDefinition,
    ProcedureError,
    ProcedureRunner,
    Status,
    Step,
    StepParams,
    list_step_types,
    step_catalog,
)
from lab_procedure.schema import RoleRef, StepClass, Value


class Emit(Step):
    """Record ``value`` from ``source`` under ``field``: a stand-in for a reading."""

    def __init__(self, source: Any, value: float, field: str = "reading", name: str | None = None) -> None:
        super().__init__(name=name)
        self.source = source
        self.value = value
        self.field = field

    def run(self) -> Status:
        assert self.context is not None
        self.context.observe({self.field: self.source.scale * self.value})
        return Status.SUCCESS


class EmitStepParams(StepParams):
    """Record a reading."""

    type: Literal["test_emit"] = "test_emit"
    source: RoleRef
    value: Value
    field: str = Field(default="reading", json_schema_extra={"column": "records"})

    @classmethod
    def step_class(cls) -> StepClass:
        return Emit

    def emitted_fields(self) -> tuple[str, ...]:
        return (self.field,)


class Scaler:
    scale = 10.0


def _definition(body: dict[str, Any]) -> ProcedureDefinition:
    return ProcedureDefinition.model_validate({
        "name": "probe",
        "roles": {"source": {"behavior": "Anything"}},
        "params": {
            "sweep": {"type": "sweep", "default": {"mode": "waypoints", "points": [0.0, 2.0, 0.0], "step": 1.0}},
            "offset": {"type": "float", "default": 0.5},
        },
        "body": body,
    })


def _rows(step: Step) -> list[dict[str, Any]]:
    runner = ProcedureRunner()
    rows: list[dict[str, Any]] = []
    runner.context.data_bus.subscribe(Point, lambda p: rows.append(p.values))
    assert runner.run(step) is Status.SUCCESS
    return rows


SWEPT = {
    "type": "sweep",
    "parameter": "bias",
    "values": {"param": "sweep"},
    "body": {"type": "sequence", "children": [
        {"type": "test_emit", "source": {"role": "source"}, "value": {"swept": "bias"}},
        {"type": "test_emit", "source": {"role": "source"}, "value": {"param": "offset"}, "field": "offset_reading"},
    ]},
}


def test_a_definition_builds_a_step_tree_that_runs():
    rows = _rows(_definition(SWEPT).build({"source": Scaler()}))
    assert rows == [
        {"bias": v, "bias_leg": leg, "reading": 10.0 * v, "offset_reading": 5.0}
        for v, leg in [(0.0, 0), (1.0, 0), (2.0, 0), (1.0, 1), (0.0, 1)]
    ]


def test_params_given_replace_the_defaults():
    step = _definition(SWEPT).build(
        {"source": Scaler()},
        {"sweep": {"mode": "explicit", "values": [3.0]}, "offset": 1.0},
    )
    assert _rows(step) == [{"bias": 3.0, "reading": 30.0, "offset_reading": 10.0}]


def test_build_and_render_make_the_same_tree():
    """The two ways out of a definition read one constructor signature, so they agree."""
    definition = _definition({"type": "repeat", "count": 2, "body": SWEPT})
    expr, ctx = definition.render_body()
    namespace: dict[str, Any] = {"source": Scaler(), "params": definition.params_model()()}
    for module, name in ctx.imports:
        namespace[name] = getattr(importlib.import_module(module), name)
    rendered = eval(expr, namespace)  # noqa: S307 - our own generated expression
    assert _rows(rendered) == _rows(definition.build({"source": Scaler()}))


def test_build_refuses_a_definition_that_does_not_check():
    definition = _definition({"type": "test_emit", "source": {"role": "nobody"}, "value": 1.0})
    with pytest.raises(ProcedureError, match="role 'nobody', which the procedure does not declare"):
        definition.build({"source": Scaler()})


def test_build_needs_an_instrument_for_every_role():
    with pytest.raises(ProcedureError, match="No instrument is bound to role 'source'"):
        _definition(SWEPT).build({})


def test_roles_are_checked_against_behaviors_only_when_given():
    class Anything: ...

    definition = _definition(SWEPT)
    assert definition.diagnose() == []
    assert definition.diagnose({"Anything": Anything}) == []
    assert "Unknown behavior 'Anything'" in definition.diagnose({})[0][1]


def test_a_condition_on_a_field_nothing_records_is_a_problem():
    definition = _definition({"type": "sequence", "children": [
        {"type": "test_emit", "source": {"role": "source"}, "value": 1.0},
        {"type": "value_above", "field": "missing", "threshold": 0.0},
    ]})
    assert "reads 'missing', which no step in this procedure records" in definition.diagnose()[0][1]


def test_defining_a_schema_registers_it_and_the_catalog_describes_it():
    assert "test_emit" in list_step_types()
    fields = step_catalog()["test_emit"]["fields"]
    assert fields["source"]["kind"] == "role"
    assert fields["field"]["column"] == "records"


def test_a_step_type_cannot_be_taken_twice():
    with pytest.raises(TypeError, match="already"):
        class Impostor(StepParams):  # noqa: F841
            type: Literal["test_emit"] = "test_emit"


def test_lab_procedure_stands_alone():
    """Nothing in lab_procedure reaches back into lab_wizard."""
    code = "import sys, lab_procedure; print(sorted(m for m in sys.modules if m.startswith('lab_wizard')))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


# --------------------------- with lab_wizard's steps ---------------------------


def test_a_lab_wizard_procedure_builds_with_stand_in_instruments(tmp_path):
    from lab_wizard.lib.instruments.general.counter import StandInCounter
    from lab_wizard.lib.instruments.general.vsource import StandInVSource
    from lab_wizard.lib.procedures.storage import load_procedure

    definition = load_procedure(tmp_path, "pcr_curve")  # an empty workspace: the built-in
    step = definition.build(
        {"voltage_source": StandInVSource(), "counter": StandInCounter()},
        {
            "bias": {"sweep": {"mode": "explicit", "values": [0.1, 0.2]}, "settle_s": 0.0},
            "readout": {"gate_time_s": 0.01},
        },
    )
    rows = _rows(step)
    assert [row["bias_voltage"] for row in rows] == [0.1, 0.2]
    assert {"counts", "count_rate"} <= set(rows[0])
