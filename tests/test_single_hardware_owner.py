"""Phase 2: one process owns each transport, and lets go of it cleanly."""

import threading
import time

import pytest
from pydantic import BaseModel, Field

from lab_wizard.lib.client.server_discovery import (
    local_endpoints,
    workspace_ipc_endpoint,
)
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer


# --------------------------- endpoint derivation ---------------------------


def test_ipc_endpoint_is_stable_and_workspace_scoped(tmp_path):
    """Server and client must derive the same path without talking first."""
    a = tmp_path / "wsA" / "config"
    b = tmp_path / "wsB" / "config"
    a.mkdir(parents=True)
    b.mkdir(parents=True)

    assert workspace_ipc_endpoint(a) == workspace_ipc_endpoint(str(a) + "/")
    assert workspace_ipc_endpoint(a) != workspace_ipc_endpoint(b)


def test_ipc_path_stays_within_socket_length_limit(tmp_path):
    """A deep workspace must not overflow the ~104-byte sun_path limit.

    This is why the path is hashed rather than embedded.
    """
    deep = tmp_path.joinpath(*["a-very-long-directory-name"] * 12, "config")
    deep.mkdir(parents=True)
    endpoint = workspace_ipc_endpoint(deep)
    assert len(endpoint[len("ipc://"):]) < 100


def test_local_endpoints_prefer_ipc(tmp_path):
    config = tmp_path / "config"
    (config / "server").mkdir(parents=True)
    (config / "server" / "server.yaml").write_text(
        "server:\n  bind: tcp://0.0.0.0:12399\n"
    )
    endpoints = local_endpoints(config)
    assert endpoints[0].startswith("ipc://")
    # 0.0.0.0 is a bind address, not something a client can dial.
    assert endpoints[1] == "tcp://127.0.0.1:12399"


# --------------------------- teardown ---------------------------


class _Dep:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _Inst:
    """Root whose handle lives on a dependency, as real instruments do."""

    def __init__(self, dep):
        self.dep = dep
        self.children = {}

    def make_child(self, key):
        child = _Inst(self.dep)
        self.children[key] = child
        return child


# Opened dependencies, so a test can assert the handle was actually closed.
# Module-level because pydantic claims underscore-prefixed class attributes.
OPENED_DEPS: list = []


class _Root(BaseModel):
    type: str = "fake"
    children: dict = Field(default_factory=dict)

    @property
    def inst(self):
        return _Inst

    def create_inst(self):
        dep = _Dep()
        OPENED_DEPS.append(dep)
        return _Inst(dep)

    def transport_sharing(self):
        return "exclusive"

    def state_authority(self):
        return "inferred"

    def transport_key(self):
        return "serial:///dev/fake"


def test_release_closes_the_handle_and_evicts():
    OPENED_DEPS.clear()
    registry = InstrumentRegistry.from_instruments({"aaa": _Root()})
    registry.resolve("inst://aaa")
    assert registry.list_held() == ["inst://aaa"]

    released = registry.release("inst://aaa")
    assert released == ["inst://aaa"]
    assert OPENED_DEPS[0].closed is True
    assert registry.list_held() == []


def test_release_is_eviction_not_removal():
    """The path stays servable; the next call reopens it."""
    OPENED_DEPS.clear()
    registry = InstrumentRegistry.from_instruments({"aaa": _Root()})
    registry.resolve("inst://aaa")
    registry.release("inst://aaa")

    assert "inst://aaa" in registry.list_paths()
    registry.resolve("inst://aaa")
    assert registry.list_held() == ["inst://aaa"]
    assert len(OPENED_DEPS) == 2  # opened a second time


def test_release_goes_deepest_first():
    """Children must let go before the root whose transport they borrow."""
    order = []

    class Tracking(_Inst):
        def __init__(self, dep, name):
            super().__init__(dep)
            self.name = name

        def disconnect(self):
            order.append(self.name)

    registry = InstrumentRegistry.from_instruments({})
    registry._index = {
        "inst://a": Tracking(_Dep(), "root"),
        "inst://a/b": Tracking(_Dep(), "child"),
        "inst://a/b/channel/0": Tracking(_Dep(), "channel"),
    }
    registry.release("inst://a")
    assert order == ["channel", "child", "root"]


def test_teardown_survives_a_broken_instrument():
    """One instrument refusing to close must not strand the others."""

    class Angry:
        def disconnect(self):
            raise RuntimeError("nope")

    closed = _Dep()

    class Fine:
        def __init__(self):
            self.dep = closed

    registry = InstrumentRegistry.from_instruments({})
    registry._index = {"inst://a": Angry(), "inst://b": Fine()}
    registry.release_all()
    assert registry.list_held() == []
    assert closed.closed is True


# --------------------------- transport locking ---------------------------


def test_lock_is_per_root_not_per_path():
    registry = InstrumentRegistry.from_instruments({"aaa": _Root(), "bbb": _Root()})
    assert registry.transport_lock("inst://aaa") is registry.transport_lock(
        "inst://aaa/x/channel/2"
    )
    assert registry.transport_lock("inst://aaa") is not registry.transport_lock(
        "inst://bbb"
    )


def test_calls_on_one_root_serialize_but_different_roots_overlap():
    """The lock must serialize a whole transaction, not just dispatch.

    A driver method is several writes against connection-global state; if two
    callers on one bus interleave, one's reply is read by the other. Different
    buses have no reason to wait on each other.
    """
    overlap = {"same_root": False, "cross_root": False}
    active: set[str] = set()
    guard = threading.Lock()

    class Slow:
        def __init__(self, root):
            self.root = root

        def work(self):
            with guard:
                if self.root in active:
                    overlap["same_root"] = True
                if active and self.root not in active:
                    overlap["cross_root"] = True
                active.add(self.root)
            time.sleep(0.05)
            with guard:
                active.discard(self.root)
            return True

    registry = InstrumentRegistry.from_instruments({})
    registry._index = {"inst://a": Slow("a"), "inst://b": Slow("b")}
    server = WireServer(bind="inproc://unused", registry=registry)

    threads = [
        threading.Thread(target=server.call, args=(p, "work"))
        for p in ("inst://a", "inst://a", "inst://b", "inst://b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlap["same_root"] is False, "two calls interleaved on one transport"
    assert overlap["cross_root"] is True, "separate transports were serialized"


def test_wire_server_requires_a_bind():
    registry = InstrumentRegistry.from_instruments({})
    with pytest.raises(ValueError):
        WireServer(bind=[], registry=registry)


def test_wire_server_accepts_multiple_binds():
    registry = InstrumentRegistry.from_instruments({})
    server = WireServer(bind=["tcp://127.0.0.1:1", "ipc:///tmp/x"], registry=registry)
    assert server.binds == ["tcp://127.0.0.1:1", "ipc:///tmp/x"]
