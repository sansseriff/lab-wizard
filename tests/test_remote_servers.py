"""Tests for the Remote Servers registry (consuming-side address book).

The live-connection paths are exercised against a real ``WireServer`` in a
background thread, backed by stand-in instruments (no hardware).
"""

from __future__ import annotations

import socket
import threading
import time
from pathlib import Path
from typing import Iterator

import pytest

from lab_wizard.lib.instruments.general.vsense import StandInVSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.wizard.backend.remote_servers import (
    add_remote_server,
    list_remote_attributes,
    load_remote_servers,
    remove_remote_server,
    remote_matches_for_base_type,
    save_remote_servers,
    test_connection as check_connection,
)


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Server:
    def __init__(self) -> None:
        reg = InstrumentRegistry.__new__(InstrumentRegistry)
        reg._index = {
            f"{PATH_PREFIX}vsource": StandInVSource(),
            f"{PATH_PREFIX}vsense": StandInVSense(),
        }
        reg._attribute_index = {
            "bias": f"{PATH_PREFIX}vsource",
            "sense": f"{PATH_PREFIX}vsense",
        }
        self.bind = f"tcp://127.0.0.1:{_free_tcp_port()}"
        self.server = WireServer(bind=self.bind, registry=reg, gate=None)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.3)

    def close(self) -> None:
        self.server.stop()
        self._thread.join(timeout=2)


@pytest.fixture
def live_server() -> Iterator[_Server]:
    srv = _Server()
    try:
        yield srv
    finally:
        srv.close()


# --------------------------- file CRUD ---------------------------


def test_add_load_remove(tmp_path: Path):
    cd = tmp_path / "config"
    assert load_remote_servers(cd) == []

    add_remote_server(cd, "cryo", "tcp://10.0.0.5:12300")
    add_remote_server(cd, "probe", "tcp://10.0.0.6:12300")
    servers = load_remote_servers(cd)
    assert {s["name"] for s in servers} == {"cryo", "probe"}

    # Adding the same name updates in place rather than duplicating.
    add_remote_server(cd, "cryo", "tcp://10.0.0.99:12300")
    servers = load_remote_servers(cd)
    assert len(servers) == 2
    assert next(s for s in servers if s["name"] == "cryo")["url"] == "tcp://10.0.0.99:12300"

    remove_remote_server(cd, "cryo")
    assert {s["name"] for s in load_remote_servers(cd)} == {"probe"}


def test_save_rejects_blank_and_duplicates(tmp_path: Path):
    cd = tmp_path / "config"
    with pytest.raises(ValueError):
        save_remote_servers(cd, [{"name": "", "url": "tcp://x"}])
    with pytest.raises(ValueError):
        save_remote_servers(
            cd, [{"name": "a", "url": "tcp://x"}, {"name": "a", "url": "tcp://y"}]
        )


# --------------------------- live connection ---------------------------


def test_test_connection_reports_attributes(live_server: _Server):
    result = check_connection(live_server.bind)
    assert result["ok"] is True
    names = {a["attribute_name"] for a in result["attributes"]}
    assert names == {"bias", "sense"}


def test_test_connection_unreachable_is_graceful():
    result = check_connection(f"tcp://127.0.0.1:{_free_tcp_port()}", timeout_ms=500)
    assert result["ok"] is False
    assert "error" in result


def test_list_and_match_remote_attributes(tmp_path: Path, live_server: _Server):
    cd = tmp_path / "config"
    add_remote_server(cd, "lab", live_server.bind)

    attrs = list_remote_attributes(cd)
    assert {a["attribute"] for a in attrs} == {"bias", "sense"}
    assert all(a["server_name"] == "lab" for a in attrs)

    vsources = remote_matches_for_base_type(cd, "VSource")
    assert [a["attribute"] for a in vsources] == ["bias"]
    vsenses = remote_matches_for_base_type(cd, "VSense")
    assert [a["attribute"] for a in vsenses] == ["sense"]


def test_list_skips_unreachable_servers(tmp_path: Path, live_server: _Server):
    cd = tmp_path / "config"
    add_remote_server(cd, "lab", live_server.bind)
    add_remote_server(cd, "dead", f"tcp://127.0.0.1:{_free_tcp_port()}")
    # The dead server is skipped; the live one still reports.
    attrs = list_remote_attributes(cd, timeout_ms=500)
    assert {a["attribute"] for a in attrs} == {"bias", "sense"}
