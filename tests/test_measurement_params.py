"""Tests for the typed measurement param models and their YAML round-trip."""

import pytest

from lab_wizard.lib.measurements.general.sweep_params import (
    ExplicitSweepParams,
    LinearSweepParams,
)
from lab_wizard.lib.measurements.iv_curve.iv_curve_params import IVCurveParams
from lab_wizard.lib.measurements.pcr_curve.pcr_curve_params import PCRCurveParams
from lab_wizard.wizard.backend.project_generation import _measurement_param_defaults


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
    assert isinstance(params.bias.sweep, LinearSweepParams)


@pytest.mark.parametrize(
    "name,model",
    [("iv_curve", IVCurveParams), ("pcr_curve", PCRCurveParams)],
)
def test_generator_defaults_round_trip_through_model(name, model):
    """The wizard's YAML defaults must validate back into the typed model and
    equal the model's own defaults — the anti-drift guarantee."""
    defaults = _measurement_param_defaults(name)
    assert model.model_validate(defaults) == model()


def test_unknown_measurement_has_no_param_defaults():
    assert _measurement_param_defaults("does_not_exist") == {}
