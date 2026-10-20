"""Unit tests for the permission state machine (server-side, no network)."""

from __future__ import annotations

import pytest

from lab_wizard.lib.instruments.general.state_effects import Arg, Kwarg, Result
from lab_wizard.lib.server.permissions import (
    Condition,
    DenyClause,
    PermissionGate,
    StateTracker,
    load_permissions,
    resolve_attributes,
)


# --------------------------- StateTracker ---------------------------


class _Stateful:
    _state_methods_ = {
        "set_voltage": ("voltage", Arg(0)),
        "turn_on": ("output", "on"),
        "turn_off": ("output", "off"),
        "set_named": ("named", Kwarg("value")),
        "read_back": ("last_read", Result()),
    }


def test_state_defaults_seeded():
    tracker = StateTracker({"inst://a": {"voltage": 0.0, "output": "off"}})
    assert tracker.get("inst://a", "voltage") == 0.0
    assert tracker.get("inst://a", "output") == "off"
    assert tracker.get("inst://a", "missing") is None


def test_record_arg_literal_kwarg_result():
    tracker = StateTracker()
    obj = _Stateful()
    tracker.record("inst://a", obj, "set_voltage", [1.5], {}, True)
    assert tracker.get("inst://a", "voltage") == 1.5

    tracker.record("inst://a", obj, "turn_on", [], {}, True)
    assert tracker.get("inst://a", "output") == "on"

    tracker.record("inst://a", obj, "turn_off", [], {}, True)
    assert tracker.get("inst://a", "output") == "off"

    tracker.record("inst://a", obj, "set_named", [], {"value": 42}, None)
    assert tracker.get("inst://a", "named") == 42

    tracker.record("inst://a", obj, "read_back", [], {}, 3.14)
    assert tracker.get("inst://a", "last_read") == 3.14


def test_record_ignores_unknown_method_and_plain_object():
    tracker = StateTracker()
    tracker.record("inst://a", _Stateful(), "not_declared", [1], {}, None)
    assert tracker.get("inst://a", "not_declared") is None
    # An object with no _state_methods_ records nothing.
    tracker.record("inst://b", object(), "set_voltage", [1], {}, None)
    assert tracker.snapshot() == {}


# --------------------------- Condition evaluation ---------------------------


def _state(**pairs) -> StateTracker:
    tracker = StateTracker()
    for compound_key, value in pairs.items():
        path, key = compound_key.split("##")
        tracker._state[(path, key)] = value  # pyright: ignore[reportPrivateUsage]
    return tracker


def test_leaf_equals_and_not_equals():
    s = _state(**{"inst://a##output": "on"})
    assert Condition.model_validate({"path": "inst://a", "key": "output", "equals": "on"}).evaluate(s)
    assert not Condition.model_validate({"path": "inst://a", "key": "output", "equals": "off"}).evaluate(s)
    assert Condition.model_validate({"path": "inst://a", "key": "output", "not_equals": "off"}).evaluate(s)


def test_leaf_numeric_comparisons():
    s = _state(**{"inst://a##voltage": 0.8})
    assert Condition.model_validate({"path": "inst://a", "key": "voltage", "greater_than": 0.0}).evaluate(s)
    assert not Condition.model_validate({"path": "inst://a", "key": "voltage", "greater_than": 1.0}).evaluate(s)
    assert Condition.model_validate({"path": "inst://a", "key": "voltage", "less_than": 1.0}).evaluate(s)


def test_leaf_in():
    s = _state(**{"inst://a##mode": "pulsing"})
    assert Condition.model_validate({"path": "inst://a", "key": "mode", "in": ["idle", "pulsing"]}).evaluate(s)
    assert not Condition.model_validate({"path": "inst://a", "key": "mode", "in": ["idle"]}).evaluate(s)


def test_leaf_unset_state_is_falsey():
    s = _state()
    # equals against None-state is False (safe default: unknown -> no match)
    assert not Condition.model_validate({"path": "inst://a", "key": "voltage", "greater_than": 0.0}).evaluate(s)


def test_composite_all_any_not():
    s = _state(**{"inst://a##voltage": 0.8, "inst://b##output": "off"})
    all_cond = Condition.model_validate({
        "all": [
            {"path": "inst://a", "key": "voltage", "greater_than": 0.0},
            {"path": "inst://b", "key": "output", "equals": "off"},
        ]
    })
    assert all_cond.evaluate(s)

    any_cond = Condition.model_validate({
        "any": [
            {"path": "inst://a", "key": "voltage", "greater_than": 5.0},  # false
            {"path": "inst://b", "key": "output", "equals": "off"},        # true
        ]
    })
    assert any_cond.evaluate(s)

    not_cond = Condition.model_validate({
        "not": {"path": "inst://b", "key": "output", "equals": "on"}
    })
    assert not_cond.evaluate(s)


def test_condition_rejects_mixed_and_empty():
    with pytest.raises(ValueError):
        Condition.model_validate({"all": [], "path": "inst://a", "key": "v"})
    with pytest.raises(ValueError):
        Condition.model_validate({})
    with pytest.raises(ValueError):
        Condition.model_validate({"path": "inst://a"})  # missing key


# --------------------------- DenyClause matching ---------------------------


def test_deny_clause_exact_path():
    clause = DenyClause.model_validate({"path": "inst://a/ch/2", "methods": ["set_voltage"]})
    assert clause.matches("inst://a/ch/2", "set_voltage")
    assert not clause.matches("inst://a/ch/3", "set_voltage")
    assert not clause.matches("inst://a/ch/2", "turn_on")


def test_deny_clause_glob():
    clause = DenyClause.model_validate({"path_glob": "inst://*/funcgen/*", "methods": ["pulse", "burst"]})
    assert clause.matches("inst://x/funcgen/0", "pulse")
    assert clause.matches("inst://y/funcgen/3", "burst")
    assert not clause.matches("inst://x/dac/0", "pulse")
    assert not clause.matches("inst://x/funcgen/0", "set_voltage")


def test_deny_clause_requires_exactly_one_path_form():
    with pytest.raises(ValueError):
        DenyClause.model_validate({"methods": ["pulse"]})
    with pytest.raises(ValueError):
        DenyClause.model_validate({"path": "a", "path_glob": "b", "methods": ["pulse"]})


# --------------------------- PermissionGate ---------------------------


def _cryo_gate() -> PermissionGate:
    cfg = load_permissions({
        "state_defaults": {"inst://a/channel/0": {"voltage": 0.0}},
        "rules": [{
            "id": "cryo_amp_safety",
            "description": "no pulsing while biased",
            "when": {"all": [{"path": "inst://a/channel/0", "key": "voltage", "greater_than": 0.0}]},
            "deny": [
                {"path_glob": "inst://*/funcgen/*", "methods": ["pulse", "burst"]},
                {"path": "inst://a/channel/2", "methods": ["set_voltage"]},
            ],
            "message": "Bias on; disable channel 0 first.",
        }],
    })
    return PermissionGate(cfg)


def test_gate_allows_when_condition_false():
    gate = _cryo_gate()
    assert gate.check("inst://x/funcgen/0", "pulse", [], {}) is None
    assert gate.check("inst://a/channel/2", "set_voltage", [0.1], {}) is None


def test_gate_denies_when_condition_true():
    gate = _cryo_gate()
    gate.record("inst://a/channel/0", _Stateful(), "set_voltage", [0.8], {}, True)

    denial = gate.check("inst://x/funcgen/0", "pulse", [], {})
    assert denial is not None
    assert denial.rule_id == "cryo_amp_safety"
    assert denial.message == "Bias on; disable channel 0 first."
    assert denial.blocking_state == {"inst://a/channel/0#voltage": 0.8}

    # The specific protected channel is also blocked.
    assert gate.check("inst://a/channel/2", "set_voltage", [0.1], {}) is not None
    # An unrelated method on funcgen is still allowed.
    assert gate.check("inst://x/funcgen/0", "configure", [], {}) is None


def test_gate_reopens_after_state_cleared():
    gate = _cryo_gate()
    gate.record("inst://a/channel/0", _Stateful(), "set_voltage", [0.8], {}, True)
    assert gate.check("inst://x/funcgen/0", "pulse", [], {}) is not None
    # Drop bias back to zero.
    gate.record("inst://a/channel/0", _Stateful(), "set_voltage", [0.0], {}, True)
    assert gate.check("inst://x/funcgen/0", "pulse", [], {}) is None


def test_empty_permissions_allows_everything():
    gate = PermissionGate(load_permissions(None))
    assert gate.check("inst://anything", "any_method", [1, 2], {"k": "v"}) is None


# --------------------------- attribute-name references ---------------------------


def test_condition_accepts_attribute_or_path_not_both():
    # attribute alone is valid
    Condition.model_validate({"attribute": "bias", "key": "voltage", "equals": 0.0})
    # path alone is valid
    Condition.model_validate({"path": "inst://a", "key": "voltage", "equals": 0.0})
    # both is invalid
    with pytest.raises(ValueError):
        Condition.model_validate(
            {"attribute": "bias", "path": "inst://a", "key": "voltage", "equals": 0.0}
        )
    # attribute without key is invalid
    with pytest.raises(ValueError):
        Condition.model_validate({"attribute": "bias"})


def test_deny_clause_accepts_exactly_one_reference_form():
    DenyClause.model_validate({"attribute": "pulse_gen", "methods": ["pulse"]})
    with pytest.raises(ValueError):
        DenyClause.model_validate(
            {"attribute": "x", "path": "inst://a", "methods": ["pulse"]}
        )
    with pytest.raises(ValueError):
        DenyClause.model_validate(
            {"attribute": "x", "path_glob": "inst://*", "methods": ["pulse"]}
        )


def test_resolve_attributes_rewrites_paths():
    cfg = load_permissions(
        {
            "rules": [
                {
                    "id": "r1",
                    "when": {
                        "all": [{"attribute": "bias", "key": "voltage", "greater_than": 0.0}]
                    },
                    "deny": [{"attribute": "pulse_gen", "methods": ["pulse"]}],
                    "message": "no",
                }
            ]
        }
    )
    amap = {"bias": "inst://x/channel/0", "pulse_gen": "inst://y/funcgen/0"}
    resolve_attributes(cfg, lambda n: amap[n])

    leaf = cfg.rules[0].when.all_[0]
    assert leaf.path == "inst://x/channel/0"
    assert leaf.attribute == "bias"  # original attribute kept for reference
    assert cfg.rules[0].deny[0].path == "inst://y/funcgen/0"


def test_gate_enforces_attribute_rules_end_to_end():
    cfg = load_permissions(
        {
            "rules": [
                {
                    "id": "cryo",
                    "when": {"attribute": "bias", "key": "voltage", "greater_than": 0.0},
                    "deny": [{"attribute": "pulse_gen", "methods": ["pulse"]}],
                    "message": "bias on",
                }
            ]
        }
    )
    amap = {"bias": "inst://x/channel/0", "pulse_gen": "inst://y/funcgen/0"}
    gate = PermissionGate(cfg, attribute_resolver=lambda n: amap[n])

    assert gate.check("inst://y/funcgen/0", "pulse", [], {}) is None
    gate.record("inst://x/channel/0", _Stateful(), "set_voltage", [0.8], {}, True)
    assert gate.check("inst://y/funcgen/0", "pulse", [], {}) is not None


def test_resolve_attributes_unknown_name_raises():
    cfg = load_permissions(
        {
            "rules": [
                {
                    "id": "r1",
                    "when": {"attribute": "ghost", "key": "v", "equals": 1},
                    "deny": [{"path": "inst://a", "methods": ["m"]}],
                }
            ]
        }
    )

    def resolver(name: str) -> str:
        raise KeyError(name)

    with pytest.raises(ValueError, match="unknown attribute_name"):
        resolve_attributes(cfg, resolver)


def test_path_only_rules_still_work_without_resolver():
    # Mixing styles: a path-based condition needs no resolution.
    gate = _cryo_gate()
    gate.record("inst://a/channel/0", _Stateful(), "set_voltage", [0.8], {}, True)
    assert gate.check("inst://x/funcgen/0", "pulse", [], {}) is not None
