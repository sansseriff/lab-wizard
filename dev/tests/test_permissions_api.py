"""Tests for the Manage Permissions backend (introspection + server.yaml IO)."""

from __future__ import annotations

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from lab_wizard.wizard.backend.permissions_api import (
    get_permissions_model,
    save_permissions,
)
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import (
    instrument_hash,
    save_instruments_to_config,
)


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A config tree populated like the instrument-initialization UI."""
    dst = tmp_path / "config"
    root_key = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    rack_key = instrument_hash("sim900", "5")
    leaf_key = instrument_hash("sim928", "1")
    save_instruments_to_config(
        {
            root_key: PrologixGPIBParams(
                port="/dev/ttyUSB0",
                children={
                    rack_key: Sim900Params(
                        gpib_address="5",
                        children={leaf_key: Sim928Params(slot="1")},
                    )
                },
            )
        },
        dst,
    )
    server_dir = dst / "server"
    server_dir.mkdir(parents=True)
    yaml = YAML(typ="rt")
    with (server_dir / "server.yaml").open("w", encoding="utf-8") as stream:
        yaml.dump(
            {"server": {"bind": "tcp://127.0.0.1:12300"}, "permissions": {"rules": []}},
            stream,
        )
    return dst


def test_model_exposes_state_keys_and_methods(config_dir: Path):
    model = get_permissions_model(config_dir)
    by_path = {i["path"]: i for i in model["instruments"]}

    # A Dac4D channel is a VSource: it records "voltage" and exposes set_voltage.
    path = "/".join(
        [
            "inst:/",
            instrument_hash("prologix_gpib", "/dev/ttyUSB0"),
            instrument_hash("sim900", "5"),
            instrument_hash("sim928", "1"),
        ]
    )
    chan = by_path[path]
    assert chan["behavior_abc"] == "VSource"
    assert "voltage" in chan["state_keys"]
    assert "set_voltage" in chan["methods"]


def test_model_includes_current_permissions(config_dir: Path):
    model = get_permissions_model(config_dir)
    # A fresh server config has an empty, editable rule list.
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
