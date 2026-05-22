"""Tests for the Manage Permissions backend (introspection + server.yaml IO)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from lab_wizard.wizard.backend.permissions_api import (
    get_permissions_model,
    save_permissions,
)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the packaged config tree."""
    dst = tmp_path / "config"
    shutil.copytree("lab_wizard/config", dst)
    return dst


def test_model_exposes_state_keys_and_methods(config_dir: Path):
    model = get_permissions_model(config_dir)
    by_path = {i["path"]: i for i in model["instruments"]}

    # A Dac4D channel is a VSource: it records "voltage" and exposes set_voltage.
    chan = by_path["inst://2da0863e/a0da5bfa/channel/0"]
    assert chan["behavior_abc"] == "VSource"
    assert "voltage" in chan["state_keys"]
    assert "set_voltage" in chan["methods"]


def test_model_includes_current_permissions(config_dir: Path):
    model = get_permissions_model(config_dir)
    # The packaged server.yaml ships one example rule.
    assert isinstance(model["permissions"].get("rules"), list)


def test_save_preserves_bind_and_persists_rules(config_dir: Path):
    perms = {
        "state_defaults": {"inst://2da0863e/a0da5bfa/channel/0": {"voltage": 0.0}},
        "rules": [
            {
                "id": "r1",
                "when": {
                    "path": "inst://2da0863e/a0da5bfa/channel/0",
                    "key": "voltage",
                    "greater_than": 0.0,
                },
                "deny": [
                    {
                        "path": "inst://2da0863e/a0da5bfa/channel/2",
                        "methods": ["set_voltage"],
                    }
                ],
                "message": "no",
            }
        ],
    }
    save_permissions(config_dir, perms)

    yaml = YAML(typ="rt")
    with open(config_dir / "server" / "server.yaml") as f:
        data = yaml.load(f)
    assert data["server"]["bind"]  # bind survived the rewrite
    assert len(data["permissions"]["rules"]) == 1
    # And the model reads it back.
    assert len(get_permissions_model(config_dir)["permissions"]["rules"]) == 1


def test_save_rejects_unknown_attribute(config_dir: Path):
    bad = {
        "rules": [
            {
                "id": "x",
                "when": {"attribute": "ghost", "key": "v", "equals": 1},
                "deny": [{"path": "inst://a", "methods": ["m"]}],
            }
        ]
    }
    with pytest.raises(ValueError, match="unknown attribute_name"):
        save_permissions(config_dir, bad)


def test_save_rejects_malformed_rule(config_dir: Path):
    # A leaf condition with neither path nor attribute is structurally invalid.
    bad = {"rules": [{"id": "x", "when": {"key": "v", "equals": 1}, "deny": []}]}
    with pytest.raises(ValueError):
        save_permissions(config_dir, bad)
