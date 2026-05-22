"""Tests for starting/stopping the instrument server from the wizard backend."""

from __future__ import annotations

import json
import shutil
import socket
import time
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from lab_wizard.wizard.backend.server_control import (
    server_status,
    set_server_bind,
    start_server,
    stop_managed_children,
    stop_server,
    suggest_free_bind,
)


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the packaged config with a self-contained server.yaml.

    server.yaml is gitignored (per-workstation), so the fixture writes its own
    rather than depending on one being present in the repo / a fresh clone.
    """
    dst = tmp_path / "config"
    shutil.copytree("lab_wizard/config", dst)
    server_dir = dst / "server"
    server_dir.mkdir(parents=True, exist_ok=True)
    yaml = YAML(typ="rt")
    data = {
        "server": {"bind": f"tcp://127.0.0.1:{_free_tcp_port()}"},
        "permissions": {
            "rules": [
                {
                    "id": "example",
                    "when": {
                        "path": "inst://2da0863e/a0da5bfa/channel/0",
                        "key": "voltage",
                        "greater_than": 0,
                    },
                    "deny": [
                        {
                            "path": "inst://2da0863e/a0da5bfa/channel/2",
                            "methods": ["set_voltage"],
                        }
                    ],
                }
            ]
        },
    }
    with open(server_dir / "server.yaml", "w") as f:
        yaml.dump(data, f)
    return dst


def test_status_stopped_by_default(config_dir: Path):
    status = server_status(config_dir)
    assert status["running"] is False
    assert status["has_config"] is True
    assert status["bind"].startswith("tcp://127.0.0.1:")
    assert status["rule_count"] == 1


def test_start_and_stop_managed(config_dir: Path):
    try:
        status = start_server(config_dir, detached=False)
        assert status["running"] is True
        assert status["detached"] is False
        assert isinstance(status["pid"], int)
        # Idempotent: starting again returns the same running server.
        again = start_server(config_dir, detached=False)
        assert again["pid"] == status["pid"]
    finally:
        stopped = stop_server(config_dir)
    assert stopped["running"] is False
    assert not (config_dir / "server" / ".server.pid").exists()


def test_start_missing_config_raises(tmp_path: Path):
    empty = tmp_path / "config"
    (empty / "server").mkdir(parents=True)
    with pytest.raises(ValueError, match="No server config"):
        start_server(empty)


def test_stale_pidfile_is_cleaned(config_dir: Path):
    # Point the pid file at a pid that is not alive.
    pidfile = config_dir / "server" / ".server.pid"
    pidfile.write_text(json.dumps({"pid": 2**31 - 1, "bind": "x", "detached": False}))
    status = server_status(config_dir)
    assert status["running"] is False
    assert not pidfile.exists()


def test_suggest_and_set_bind(config_dir: Path):
    suggested = suggest_free_bind(config_dir)
    # Host is preserved from the existing config; only the port is fresh.
    assert suggested.startswith("tcp://127.0.0.1:")
    set_server_bind(config_dir, suggested)
    assert server_status(config_dir)["bind"] == suggested


def test_set_bind_rejects_garbage(config_dir: Path):
    with pytest.raises(ValueError, match="Invalid bind"):
        set_server_bind(config_dir, "not-a-bind")


def test_prefer_default_offers_default_port_when_free(tmp_path: Path):
    # A fresh workstation with no server.yaml: the standard port is offered when
    # it is free (so the user sees the familiar address).
    fresh = tmp_path / "config"
    (fresh / "server").mkdir(parents=True)
    suggestion = suggest_free_bind(fresh, prefer_default=True)
    # Either the default port (if free on this machine) or a fallback free port.
    assert suggestion.startswith("tcp://0.0.0.0:")
    # Without prefer_default, it must be an explicitly free (non-guaranteed) port.
    assert suggest_free_bind(fresh).startswith("tcp://0.0.0.0:")


def test_start_refuses_when_port_in_use(config_dir: Path):
    bind = suggest_free_bind(config_dir)
    set_server_bind(config_dir, bind)
    port = int(bind.rsplit(":", 1)[1])
    occupier = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupier.bind(("127.0.0.1", port))
    occupier.listen()
    try:
        with pytest.raises(ValueError, match="already listening"):
            start_server(config_dir)
    finally:
        occupier.close()


def test_stop_managed_children_terminates(config_dir: Path):
    start_server(config_dir, detached=False)
    assert server_status(config_dir)["running"] is True
    # Simulates wizard shutdown for managed (non-detached) servers.
    stop_managed_children()
    # Give the OS a moment to reap.
    deadline = time.time() + 5
    while time.time() < deadline and server_status(config_dir)["running"]:
        time.sleep(0.1)
    assert server_status(config_dir)["running"] is False
