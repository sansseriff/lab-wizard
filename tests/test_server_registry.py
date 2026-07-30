"""Machine-local server registry and per-attribute resource routing."""

import json
import os

import pytest

from lab_wizard.lib.client.composite_resources import LOCAL, CompositeResources
from lab_wizard.lib.client.server_registry import (
    advertise_server,
    list_local_servers,
    local_server_endpoints,
    registry_dir,
    withdraw_server,
)


@pytest.fixture(autouse=True)
def _isolated_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "servers"))
    yield


def _workspace(tmp_path, name):
    config = tmp_path / name / "config"
    config.mkdir(parents=True)
    return config


# --------------------------- the registry ---------------------------


def test_a_server_is_findable_without_knowing_its_workspace(tmp_path):
    """The whole point: workspace B cannot derive A's endpoint.

    A's ipc path is hashed from A's config dir and its server.yaml lives
    somewhere B has no reason to look, so a workspace-scoped lookup is
    structurally blind to it. The registry is how B sees it at all.
    """
    ws_a = _workspace(tmp_path, "rack-a")
    advertise_server(ws_a, bind="tcp://0.0.0.0:12300", ipc="ipc:///tmp/a.sock")

    found = list_local_servers()
    assert len(found) == 1
    assert found[0]["config_dir"] == str(ws_a.resolve())
    assert found[0]["workspace_path"] == str(ws_a.resolve().parent)


def test_multiple_workspaces_each_get_an_entry(tmp_path):
    advertise_server(_workspace(tmp_path, "a"), ipc="ipc:///tmp/a.sock")
    advertise_server(_workspace(tmp_path, "b"), ipc="ipc:///tmp/b.sock")
    assert len(list_local_servers()) == 2


def test_re_advertising_replaces_rather_than_duplicates(tmp_path):
    ws = _workspace(tmp_path, "a")
    advertise_server(ws, ipc="ipc:///tmp/old.sock")
    advertise_server(ws, ipc="ipc:///tmp/new.sock")
    servers = list_local_servers()
    assert len(servers) == 1
    assert servers[0]["ipc"] == "ipc:///tmp/new.sock"


def test_withdraw_removes_the_entry(tmp_path):
    ws = _workspace(tmp_path, "a")
    advertise_server(ws, ipc="ipc:///tmp/a.sock")
    withdraw_server(ws)
    assert list_local_servers() == []


def test_withdraw_is_safe_when_absent(tmp_path):
    withdraw_server(_workspace(tmp_path, "a"))  # must not raise


def test_a_dead_server_is_dropped_and_reaped(tmp_path):
    """A crashed server would otherwise advertise forever.

    Callers would then dial a socket nobody is listening on, and — worse for the
    conflict check — believe hardware was held when it is free.
    """
    ws = _workspace(tmp_path, "a")
    advertise_server(ws, ipc="ipc:///tmp/a.sock")

    descriptor = next(registry_dir().glob("*.json"))
    entry = json.loads(descriptor.read_text())
    entry["pid"] = 2**30  # a pid that cannot be alive
    descriptor.write_text(json.dumps(entry))

    assert list_local_servers() == []
    assert not descriptor.exists(), "stale descriptor should be reaped"


def test_a_dead_entry_can_be_inspected_without_reaping(tmp_path):
    ws = _workspace(tmp_path, "a")
    advertise_server(ws, ipc="ipc:///tmp/a.sock", pid=2**30)
    assert list_local_servers(reap=False) == []
    assert list(registry_dir().glob("*.json"))  # still on disk


def test_unreadable_descriptors_are_ignored(tmp_path):
    """Half-written or hand-mangled files must not break discovery."""
    registry_dir().mkdir(parents=True, exist_ok=True)
    (registry_dir() / "garbage.json").write_text("{not json")
    advertise_server(_workspace(tmp_path, "a"), ipc="ipc:///tmp/a.sock")
    assert len(list_local_servers()) == 1


def test_missing_registry_directory_is_not_an_error():
    assert list_local_servers() == []


def test_endpoints_prefer_ipc_and_rewrite_wildcard_binds():
    entry = {"ipc": "ipc:///tmp/a.sock", "bind": "tcp://0.0.0.0:12300"}
    assert local_server_endpoints(entry) == [
        "ipc:///tmp/a.sock",
        "tcp://127.0.0.1:12300",
    ]


def test_endpoints_handles_a_server_without_ipc():
    assert local_server_endpoints({"bind": "tcp://0.0.0.0:1"}) == [
        "tcp://127.0.0.1:1"
    ]


# --------------------------- per-attribute routing ---------------------------


class _Source:
    def __init__(self, label):
        self.label = label
        self.closed = False

    def from_attribute(self, name):
        return f"{self.label}:{name}"

    def close(self):
        self.closed = True


def test_unmapped_attributes_default_to_local():
    """Existing projects declare no sources and must keep working."""
    composite = CompositeResources(local=_Source("local"))
    assert composite.from_attribute("bias") == "local:bias"
    assert composite.source_of("bias") == LOCAL


def test_attributes_route_to_their_declared_server():
    composite = CompositeResources(
        local=_Source("local"),
        remotes={"cryo": _Source("cryo")},
        sources={"counter": "cryo"},
    )
    assert composite.from_attribute("bias") == "local:bias"
    assert composite.from_attribute("counter") == "cryo:counter"


def test_a_missing_server_is_reported_clearly():
    composite = CompositeResources(local=_Source("local"), sources={"x": "absent"})
    with pytest.raises(ValueError, match="not connected"):
        composite.from_attribute("x")


def test_resolution_is_cached():
    local = _Source("local")
    composite = CompositeResources(local=local)
    assert composite.from_attribute("bias") is composite.from_attribute("bias")


def test_close_closes_every_remote():
    cryo, warm = _Source("cryo"), _Source("warm")
    composite = CompositeResources(remotes={"cryo": cryo, "warm": warm})
    composite.close()
    assert cryo.closed and warm.closed


def test_from_project_connects_only_referenced_servers():
    """An unused address-book entry must not be dialled."""
    connected = []

    class Project:
        class resources:  # noqa: N801 - stands in for ResourceConfig
            instrument_sources = {"counter": "cryo", "bias": "local"}

            @staticmethod
            def from_attribute(name):
                return f"local:{name}"

    def connect(url):
        connected.append(url)
        return _Source("cryo")

    composite = CompositeResources.from_project(
        Project(),
        server_urls={"cryo": "tcp://cryo:1", "unused": "tcp://nope:2"},
        connect=connect,
    )
    assert connected == ["tcp://cryo:1"]
    assert composite.from_attribute("counter") == "cryo:counter"


def test_from_project_rejects_an_unregistered_server_name():
    class Project:
        class resources:  # noqa: N801
            instrument_sources = {"counter": "ghost"}

    with pytest.raises(ValueError, match="no such server is registered"):
        CompositeResources.from_project(Project(), server_urls={}, connect=lambda u: None)
