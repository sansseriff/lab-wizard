"""Phases 8/7/6: who may reconfigure, claims on transports, client recovery."""

import os
import threading
import time

import pytest
from pydantic import BaseModel, Field

from lab_wizard.lib.client import leases
from lab_wizard.lib.client.leases import LeaseHeld
from lab_wizard.lib.server.events import EventLog
from lab_wizard.lib.server.peer import (
    LocalOnlyError,
    Peer,
    require_local,
    reset_current_peer,
    set_current_peer,
)
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_LEASE_DIR", str(tmp_path / "leases"))
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "servers"))
    yield


# --------------------------- authority ---------------------------


def test_ipc_peers_are_local_and_tcp_peers_are_not():
    assert Peer(transport="ipc", identity="a").is_local is True
    assert Peer(transport="tcp", identity="a").is_local is False


def test_require_local_rejects_a_remote_peer():
    token = set_current_peer(Peer(transport="tcp", identity="abc", name="rack-b"))
    try:
        with pytest.raises(LocalOnlyError, match="only available to clients on this machine"):
            require_local("Adding instruments")
    finally:
        reset_current_peer(token)


def test_require_local_allows_in_process_callers():
    """No peer means nothing came over a socket — that is the local path."""
    assert require_local("anything").is_local is True


def test_a_same_machine_client_on_tcp_gets_the_smaller_set():
    """Privilege is opted into by using the local socket, never ambient.

    A client on this machine that dials tcp is treated as remote, so the
    boundary stays a property of the receive path rather than of geography.
    """
    token = set_current_peer(Peer(transport="tcp", identity="local-but-tcp"))
    try:
        with pytest.raises(LocalOnlyError):
            require_local("Removing instruments")
    finally:
        reset_current_peer(token)


def test_wire_server_splits_sockets_by_transport():
    """One socket per transport, because ZMQ cannot report arrival endpoint."""
    registry = InstrumentRegistry.from_instruments({})
    server = WireServer(
        bind=["ipc:///tmp/a.sock", "tcp://127.0.0.1:1"], registry=registry
    )
    assert server._ipc_binds == ["ipc:///tmp/a.sock"]
    assert server._tcp_binds == ["tcp://127.0.0.1:1"]


# --------------------------- the event log ---------------------------


def test_events_are_recorded_and_survive_restart(tmp_path):
    path = tmp_path / "events.jsonl"
    log = EventLog(path)
    log.record("tree.add", "Added a dbay", actor="rack-b (ipc)", key="abc")

    reopened = EventLog(path)
    recent = reopened.recent()
    assert len(recent) == 1
    assert recent[0]["message"] == "Added a dbay"
    assert recent[0]["actor"] == "rack-b (ipc)"


def test_events_are_newest_first_and_bounded(tmp_path):
    log = EventLog(tmp_path / "e.jsonl", memory_events=3)
    for i in range(5):
        log.record("test", f"event {i}")
    messages = [e["message"] for e in log.recent()]
    assert messages == ["event 4", "event 3", "event 2"]


def test_recording_never_raises_on_a_bad_path(tmp_path):
    """An audit note must not fail the operation it is describing."""
    log = EventLog(tmp_path / "nonexistent" / "deep" / "e.jsonl")
    log.record("test", "still fine")  # parent dirs are created
    assert len(log.recent()) == 1


# --------------------------- leases ---------------------------


def test_acquire_then_release():
    entry = leases.acquire("serial:///dev/ttyUSB0", owner="iv_curve")
    assert entry["owner"] == "iv_curve"
    assert leases.holder_of("serial:///dev/ttyUSB0") is not None
    assert leases.release("serial:///dev/ttyUSB0") is True
    assert leases.holder_of("serial:///dev/ttyUSB0") is None


def test_a_second_claimant_is_refused():
    leases.acquire("serial:///dev/ttyUSB0", owner="first", pid=os.getpid())
    with pytest.raises(LeaseHeld, match="claimed by first"):
        # A different pid, i.e. a different process wanting the same bus.
        leases.acquire("serial:///dev/ttyUSB0", owner="second", pid=os.getpid() + 1)


def test_reacquiring_our_own_claim_is_a_no_op():
    first = leases.acquire("serial:///dev/x", owner="me")
    again = leases.acquire("serial:///dev/x", owner="me")
    assert again["acquired_at"] == first["acquired_at"]


def test_a_dead_holders_claim_is_reaped():
    """A crashed process must not block a rack forever."""
    leases.acquire("serial:///dev/x", owner="ghost", pid=2**30)
    assert leases.holder_of("serial:///dev/x") is None
    # And the rack is claimable again.
    assert leases.acquire("serial:///dev/x", owner="me")["owner"] == "me"


def test_releasing_someone_elses_claim_is_refused():
    leases.acquire("serial:///dev/x", owner="them", pid=os.getpid())
    assert leases.release("serial:///dev/x", pid=os.getpid() + 1) is False
    assert leases.holder_of("serial:///dev/x") is not None


def test_claims_are_keyed_on_transport_not_config():
    """Two workspaces naming one port must contend, despite different keys."""
    leases.acquire("serial:///dev/ttyUSB0", owner="workspace-a")
    with pytest.raises(LeaseHeld):
        leases.acquire("serial:///dev/ttyUSB0", owner="workspace-b", pid=os.getpid() + 1)


def test_release_all_for_pid():
    leases.acquire("serial:///dev/a", owner="me")
    leases.acquire("serial:///dev/b", owner="me")
    assert sorted(leases.release_all_for_pid()) == ["serial:///dev/a", "serial:///dev/b"]
    assert leases.list_leases() == []


# --------------------------- server honours leases ---------------------------


class _Inst:
    def __init__(self):
        self.dep = type("D", (), {"close": lambda s: None})()

    def ping(self):
        return "pong"


class _Root(BaseModel):
    type: str = "fake"
    sharing: str = "exclusive"
    children: dict = Field(default_factory=dict)

    @property
    def inst(self):
        return _Inst

    def create_inst(self):
        return _Inst()

    def transport_sharing(self):
        return self.sharing

    def state_authority(self):
        return "inferred"

    def transport_key(self):
        return "serial:///dev/leased"


def test_server_declines_to_open_a_claimed_rack():
    registry = InstrumentRegistry.from_instruments({"aaa": _Root()})
    server = WireServer(bind="inproc://x", registry=registry)

    # pid 1 is always alive and is never us — a real "held by someone else".
    # (os.getpid() + 1 would be reaped as dead, which is the liveness check
    # working, not the lease failing.)
    leases.acquire("serial:///dev/leased", owner="a project", pid=1)
    with pytest.raises(ValueError, match="claimed by a project"):
        server.call("inst://aaa", "ping")


def test_server_still_serves_a_rack_it_already_holds():
    """A claim taken after we opened the bus does not evict us."""
    registry = InstrumentRegistry.from_instruments({"aaa": _Root()})
    server = WireServer(bind="inproc://x", registry=registry)
    assert server.call("inst://aaa", "ping") == "pong"

    leases.acquire("serial:///dev/leased", owner="latecomer", pid=1)
    assert server.call("inst://aaa", "ping") == "pong"


def test_shared_transports_ignore_claims():
    registry = InstrumentRegistry.from_instruments({"aaa": _Root(sharing="shared")})
    server = WireServer(bind="inproc://x", registry=registry)
    leases.acquire("serial:///dev/leased", owner="someone", pid=1)
    assert server.call("inst://aaa", "ping") == "pong"


# --------------------------- proxy recovery ---------------------------


class _FakeSession:
    """Session stand-in that moves an instrument to a new path once."""

    def __init__(self, old_path, new_path):
        self.old_path, self.new_path = old_path, new_path
        self.calls = []

    def call_inst(self, path, method, args, kwargs):
        self.calls.append(path)
        if path == self.old_path:
            raise RuntimeError(f"No instrument registered at path {path!r}")
        return "ok"

    def call(self, method, params=None):
        assert method == "describe_attribute"
        return {"path": self.new_path, "behavior_abc": "VSource"}


def test_a_proxy_recovers_when_its_path_changes():
    """inst:// paths are hash-derived, so a key-field edit moves an instrument.

    Without this the proxy points at nothing forever, which is the latent bug
    the mutable tree would otherwise have exposed.
    """
    from lab_wizard.lib.client.proxies.vsource import RemoteVSource

    session = _FakeSession("inst://old", "inst://new")
    proxy = RemoteVSource(session, "inst://old", "bias_vsource")

    assert proxy.set_voltage(0.5) == "ok"
    assert session.calls == ["inst://old", "inst://new"]
    # It adopts the new path, so the next call goes straight there.
    assert proxy.set_voltage(0.6) == "ok"
    assert session.calls[-1] == "inst://new"


def test_a_proxy_without_a_name_cannot_recover():
    from lab_wizard.lib.client.proxies.vsource import RemoteVSource

    session = _FakeSession("inst://old", "inst://new")
    proxy = RemoteVSource(session, "inst://old")
    with pytest.raises(RuntimeError, match="No instrument registered"):
        proxy.set_voltage(0.5)


def test_unrelated_errors_are_not_retried():
    """A driver fault or permission denial must propagate untouched."""
    from lab_wizard.lib.client.proxies.vsource import RemoteVSource

    class Angry:
        def call_inst(self, *a, **k):
            raise RuntimeError("instrument is on fire")

    proxy = RemoteVSource(Angry(), "inst://x", "name")
    with pytest.raises(RuntimeError, match="on fire"):
        proxy.set_voltage(0.5)
