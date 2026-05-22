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
    start_server,
    stop_managed_children,
    stop_server,
)


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A writable copy of the packaged config, bound to a free port."""
    dst = tmp_path / "config"
    shutil.copytree("lab_wizard/config", dst)
    yaml = YAML(typ="rt")
    server_yaml = dst / "server" / "server.yaml"
    with open(server_yaml) as f:
        data = yaml.load(f)
    data["server"]["bind"] = f"tcp://127.0.0.1:{_free_tcp_port()}"
    with open(server_yaml, "w") as f:
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
