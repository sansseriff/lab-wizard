"""Instrument params must not hold procedure choreography.

Instrument params describe *which device* and *how the bench is wired*. A sweep
— a start, a stop, a step — describes an *experiment*, and belongs in a
measurement's params, handed to the instrument as method arguments. The old
system's ``config['counterInst']['triggerLevelStart']`` is the failure this
catches: a procedure filed under an instrument, which then grows
``triggerLevelEnd`` and ``triggerLevelStep`` and becomes a sweep specification
hiding in a counter's config block.

See ``plans/procedure_plan.md`` 2.1 for the three parameter categories and the
test for telling them apart.
"""

from __future__ import annotations

import re
from collections import defaultdict

from pydantic import BaseModel

from lab_wizard.lib.utilities.resource_catalog import (
    _model_types,
    list_available_types,
    load_params_class,
)


_SWEEP_SUFFIXES = {"start": "start", "begin": "start", "stop": "stop", "end": "stop", "step": "step"}

# (ParamsClass name, field stem) -> why this is genuinely a fact about the
# instrument rather than a sweep. Empty on purpose: add to it only with a reason
# a reviewer would accept.
ALLOWED: dict[tuple[str, str], str] = {}


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).lower()


def sweep_shaped_fields(model: type[BaseModel]) -> dict[str, set[str]]:
    """Stems in ``model`` that carry two or more of start / stop / step.

    ``trigger_level_start`` + ``trigger_level_end`` → ``{"trigger_level": {"start", "stop"}}``.
    One suffix alone is not flagged: ``gate_start_slope`` is not a sweep.
    """
    roles: dict[str, set[str]] = defaultdict(set)
    for field in model.model_fields:
        parts = _snake(field).split("_")
        # Units come last in this codebase (start_V, step_V), so look past one.
        for idx in (len(parts) - 1, len(parts) - 2):
            if idx >= 0 and parts[idx] in _SWEEP_SUFFIXES:
                stem = "_".join(parts[:idx]) or "(unnamed)"
                roles[stem].add(_SWEEP_SUFFIXES[parts[idx]])
                break
    return {stem: found for stem, found in roles.items() if len(found) >= 2}


def _every_instrument_params_model() -> list[type[BaseModel]]:
    """Every instrument params class, plus the models nested in its fields
    (channel params live one level down, in ``channels: dict[int, ...]``)."""
    seen: dict[type[BaseModel], None] = {}

    def walk(model: type[BaseModel]) -> None:
        if model in seen:
            return
        seen[model] = None
        for field in model.model_fields.values():
            for nested in _model_types(field.annotation):
                walk(nested)

    for type_str in list_available_types("instrument"):
        walk(load_params_class(type_str))
    return list(seen)


def test_the_detector_catches_the_old_systems_smell():
    class OldCounterConfig(BaseModel):
        triggerLevelStart: float = 0.0
        triggerLevelEnd: float = 0.1
        triggerLevelStep: float = 0.01
        impedance: int = 50

    class SweepWithUnits(BaseModel):
        start_V: float = 0.0
        stop_V: float = 1.0
        step_V: float = 0.1

    assert sweep_shaped_fields(OldCounterConfig) == {"trigger_level": {"start", "stop", "step"}}
    # A measurement's own sweep params (LinearSweepParams) have exactly this
    # shape, legitimately — the check is only ever applied to instrument params.
    assert sweep_shaped_fields(SweepWithUnits) == {"(unnamed)": {"start", "stop", "step"}}


def test_the_detector_ignores_a_lone_suffix():
    class GateConfig(BaseModel):
        gate_start_slope: str = "positive"
        hold_off_stop: float = 0.0

    assert sweep_shaped_fields(GateConfig) == {}


def test_no_instrument_params_model_holds_a_sweep():
    models = _every_instrument_params_model()
    assert len(models) > 20, "discovery found suspiciously few params models"

    offenders = {
        f"{model.__name__}.{stem}_*": sorted(found)
        for model in models
        for stem, found in sweep_shaped_fields(model).items()
        if (model.__name__, stem) not in ALLOWED
    }
    assert not offenders, (
        f"instrument params with sweep-shaped fields: {offenders}. A start/stop/"
        "step belongs in a measurement's params, passed to the instrument as "
        "method arguments — see plans/procedure_plan.md 2.1. If this really is "
        "a fact about the instrument, add it to ALLOWED with the reason."
    )
