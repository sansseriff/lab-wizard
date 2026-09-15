"""LocalTransportClaim: lease every exclusive transport, then preflight."""

from __future__ import annotations

import os

import pytest

from lab_wizard.lib.client import leases
from lab_wizard.lib.client import local_claims
from lab_wizard.lib.client.local_claims import LocalTransportClaim, exclusive_transport_keys


class Root:
    def __init__(self, key: str | None, sharing: str = "exclusive") -> None:
        self._key, self._sharing = key, sharing

    def transport_key(self) -> str | None:
        return self._key

    def transport_sharing(self) -> str:
        return self._sharing


@pytest.fixture
def no_servers(monkeypatch):
    """Record preflight calls instead of asking this machine's real servers."""
    calls: list[dict] = []
    monkeypatch.setattr(local_claims, "preflight_local_project", lambda inst: calls.append(inst))
    return calls


def test_only_exclusive_keyed_transports_are_claimed_and_each_once():
    instruments = {
        "a": Root("serial:///dev/ttyUSB0"),
        "b": Root("serial:///dev/ttyUSB0"),  # the same device configured twice
        "c": Root("tcp://10.0.0.4:8345", sharing="shared"),
        "d": Root(None),
    }
    assert exclusive_transport_keys(instruments) == ["serial:///dev/ttyUSB0"]


def test_leases_are_held_for_the_block_and_released_after(no_servers):
    instruments = {"a": Root("serial:///dev/ttyUSB0"), "b": Root("tcp://10.0.0.9:5025")}

    with LocalTransportClaim(instruments, owner="pcr_run"):
        serial_holder = leases.holder_of("serial:///dev/ttyUSB0")
        counter_holder = leases.holder_of("tcp://10.0.0.9:5025")
        assert serial_holder is not None and serial_holder["owner"] == "pcr_run"
        assert counter_holder is not None and counter_holder["pid"] == os.getpid()
        assert no_servers == [instruments]

    assert leases.holder_of("serial:///dev/ttyUSB0") is None
    assert leases.holder_of("tcp://10.0.0.9:5025") is None


def test_a_transport_another_process_holds_is_refused_and_nothing_is_kept(no_servers):
    # Our parent is alive and is not us, so its claim is live and not ours.
    leases.acquire("tcp://10.0.0.9:5025", owner="someone else", pid=os.getppid())
    instruments = {"a": Root("serial:///dev/ttyUSB0"), "b": Root("tcp://10.0.0.9:5025")}

    with pytest.raises(leases.LeaseHeld, match="someone else"):
        with LocalTransportClaim(instruments, owner="pcr_run"):
            pytest.fail("the block must not run")

    # The lease taken before the refusal was given back.
    assert leases.holder_of("serial:///dev/ttyUSB0") is None
    assert no_servers == []


def test_a_server_already_holding_the_rack_releases_our_leases(monkeypatch):
    """Preflight runs after leasing, and its refusal must not strand the leases."""

    def server_holds_it(_instruments):
        raise RuntimeError("the instrument server already has them open")

    monkeypatch.setattr(local_claims, "preflight_local_project", server_holds_it)
    instruments = {"a": Root("serial:///dev/ttyUSB0")}

    with pytest.raises(RuntimeError, match="already has them open"):
        with LocalTransportClaim(instruments, owner="pcr_run"):
            pytest.fail("the block must not run")
    assert leases.holder_of("serial:///dev/ttyUSB0") is None


def test_server_checks_can_be_skipped(no_servers):
    with LocalTransportClaim({"a": Root("serial:///dev/ttyUSB0")}, owner="x", check_servers=False):
        pass
    assert no_servers == []


def test_a_project_with_no_instruments_asks_no_server(no_servers):
    with LocalTransportClaim({}, owner="x"):
        pass
    assert no_servers == []
