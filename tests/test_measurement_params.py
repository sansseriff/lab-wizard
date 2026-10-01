"""Tests for the typed measurement param models and their YAML round-trip."""

from pathlib import Path

import pytest

from lab_procedure.sweep import (
    ExplicitSweepParams,
    LinearSweepParams,
    WaypointSweepParams,
)
from lab_wizard.lib.procedures.storage import load_procedure

# The built-in procedures, read without a workspace of their own.
_NO_WORKSPACE = Path(__file__).parent / "no-such-config"
IVCurveParams = load_procedure(_NO_WORKSPACE, "iv_curve").params_model()


def test_linear_sweep_inclusive_ascending():
    sweep = LinearSweepParams(start_V=0.0, stop_V=1.0, step_V=0.25)
    assert sweep.values() == [0.0, 0.25, 0.5, 0.75, 1.0]


def test_linear_sweep_endpoint_included_when_not_multiple_of_step():
    sweep = LinearSweepParams(start_V=0.0, stop_V=1.0, step_V=0.3)
    values = sweep.values()
    assert values[0] == 0.0
    assert abs(values[-1] - 1.0) < 1e-12


def test_linear_sweep_descending():
    sweep = LinearSweepParams(start_V=1.0, stop_V=0.0, step_V=0.5)
    assert sweep.values() == [1.0, 0.5, 0.0]


def test_linear_sweep_single_point_when_start_equals_stop():
    assert LinearSweepParams(start_V=0.3, stop_V=0.3, step_V=0.1).values() == [0.3]


def test_linear_sweep_rejects_nonpositive_step():
    with pytest.raises(ValueError):
        LinearSweepParams(start_V=0.0, stop_V=1.0, step_V=0.0).values()


def test_explicit_sweep_returns_its_values():
    sweep = ExplicitSweepParams(values_V=[0.0, 0.1, 0.9])
    assert sweep.values() == [0.0, 0.1, 0.9]


def test_sweep_discriminator_selects_explicit():
    params = IVCurveParams.model_validate(
        {"bias": {"sweep": {"mode": "explicit", "values_V": [0.0, 0.5, 1.0]}}}
    )
    assert isinstance(params.bias.sweep, ExplicitSweepParams)
    assert params.bias.sweep.values() == [0.0, 0.5, 1.0]


def test_sweep_discriminator_selects_linear():
    params = IVCurveParams.model_validate(
        {"bias": {"sweep": {"mode": "linear", "start_V": 0.0, "stop_V": 0.2, "step_V": 0.1}}}
    )
    assert isinstance(params.bias.sweep, LinearSweepParams)
    assert params.bias.sweep.values() == [0.0, 0.1, 0.2]


def test_partial_params_fill_defaults():
    params = IVCurveParams.model_validate({})
    assert params.readout.bias_resistance_ohm == 100_000.0
    assert params.safety.return_to_zero is True
    # The IV curve's default is the loop: up, back, down, back.
    assert isinstance(params.bias.sweep, WaypointSweepParams)
    assert params.bias.sweep.points == [0.0, 1.4, 0.0, -1.4, 0.0]


def test_a_waypoint_sweep_walks_each_leg_once_through_its_turning_points():
    sweep = WaypointSweepParams(points=[0.0, 1.0, 0.0, -1.0, 0.0], step=0.5)
    assert sweep.values() == [0.0, 0.5, 1.0, 0.5, 0.0, -0.5, -1.0, -0.5, 0.0]
    # Each turning point ends the leg that reaches it.
    assert sweep.also("bias") == {"bias_leg": [0, 0, 0, 1, 1, 2, 2, 3, 3]}


def test_a_waypoint_sweep_lands_exactly_on_its_points():
    values = WaypointSweepParams(points=[0.0, 1.4, 0.0], step=0.005).values()
    assert values[280] == 1.4 and values[-1] == 0.0
    assert all(v >= 0 for v in values)  # no -0.0, no 2e-16 on the way back


def test_a_waypoint_sweep_needs_two_points_and_a_positive_step():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WaypointSweepParams(points=[1.0], step=0.1)
    with pytest.raises(ValidationError):
        WaypointSweepParams(points=[0.0, 1.0], step=0)


def test_a_sweep_records_its_leg_beside_each_value():
    from lab_procedure import ProcedureRunner, Status, Step
    from lab_procedure.messages import Point
    from lab_procedure.steps import Sweep

    class Read(Step):
        def run(self) -> Status:
            assert self.context is not None
            self.context.observe({"reading": 1.0})
            return Status.SUCCESS

    sweep = WaypointSweepParams(points=[0.0, 1.0, 0.0], step=1.0)
    runner = ProcedureRunner()
    points: list[Point] = []
    runner.context.data_bus.subscribe((Point,), points.append)
    status = runner.run(Sweep("bias", sweep.values(), lambda b: Read(), also=sweep.also("bias")))
    assert status is Status.SUCCESS
    assert [(p.values["bias"], p.values["bias_leg"]) for p in points] == [(0.0, 0), (1.0, 0), (0.0, 1)]


@pytest.mark.parametrize("name", ["iv_curve", "pcr_curve", "mcr_curve"])
def test_procedure_defaults_round_trip_through_their_model(name):
    """The YAML defaults a project is generated with must validate back into the
    params model the generated setup uses, and equal its own defaults — the
    anti-drift guarantee."""
    definition = load_procedure(_NO_WORKSPACE, name)
    model = definition.params_model()
    assert model.model_validate(definition.param_defaults()) == model()


def test_sweeps_are_unit_free_and_still_read_the_volt_named_fields():
    """The same sweep drives volts and decibels, so its fields carry no unit.
    Projects and presets written with the old names must keep loading."""
    from pydantic import TypeAdapter

    from lab_procedure.sweep import SweepParams

    adapter = TypeAdapter(SweepParams)
    old = adapter.validate_python({"mode": "linear", "start_V": 0.0, "stop_V": 20.0, "step_V": 10.0})
    new = adapter.validate_python({"mode": "linear", "start": 0.0, "stop": 20.0, "step": 10.0})
    assert old.values() == new.values() == [0.0, 10.0, 20.0]
    assert new.model_dump(mode="json") == {"mode": "linear", "start": 0.0, "stop": 20.0, "step": 10.0}

    explicit = adapter.validate_python({"mode": "explicit", "values_V": [30.0, 10.0]})
    assert explicit.values() == [30.0, 10.0]
    assert explicit.model_dump(mode="json") == {"mode": "explicit", "values": [30.0, 10.0]}


def test_an_explicit_sweep_is_written_to_yaml_under_values():
    from lab_procedure.sweep import ExplicitSweepParams
    from lab_wizard.lib.utilities.config_io import model_to_commented_map

    assert dict(model_to_commented_map(ExplicitSweepParams(values=[1.0]))) == {"mode": "explicit", "values": [1.0]}
