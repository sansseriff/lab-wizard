"""WireServer claim enforcement, called in-process (no socket)."""

from __future__ import annotations

from typing import Any

import pytest

from pyleco.json_utils.errors import JSONRPCError

from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams
from lab_wizard.lib.server.claims import ClaimTable
from lab_wizard.lib.server.peer import LocalOnlyError, Peer, reset_current_peer, set_current_peer
from lab_wizard.lib.server.permissions import PermissionGate, load_permissions
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import CLAIM_DENIED_CODE, WireServer

COUNTER = "inst://counter"
CH0, CH1 = f"{COUNTER}/channel/0", f"{COUNTER}/channel/1"


class Clock:
    def __init__(self) -> None:
        self.now = 50.0

    def __call__(self) -> float:
        return self.now


def _registry(rig) -> InstrumentRegistry:
    counter = rig.counter_params()
    counter.channels = {
        0: Keysight53220AChannelParams(threshold_mV=-50.0),
        1: Keysight53220AChannelParams(threshold_mV=-50.0),
    }
    rack = rig.gpib_params()
    # Readable paths rather than the wizard's hashed keys.
    (mainframe,) = rack.children.values()
    mainframe.children = {"source": mainframe.children[rig.source], "voltmeter": mainframe.children[rig.meter]}
    rack.children = {"mainframe": mainframe}
    return InstrumentRegistry.from_instruments({"counter": counter, "rack": rack})


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def server(clock: Clock, rig) -> WireServer:
    return WireServer(bind="inproc://claims", registry=_registry(rig), claims=ClaimTable(clock=clock))


def _denied(fn, *args: Any, **kwargs: Any) -> JSONRPCError:
    with pytest.raises(JSONRPCError) as info:
        fn(*args, **kwargs)
    assert info.value.rpc_error.code == CLAIM_DENIED_CODE
    return info.value


# --------------------------- claim units ---------------------------


@pytest.mark.parametrize(
    ("path", "unit"),
    [
        (CH0, CH0),  # 53220A inputs are claimable
        (COUNTER, COUNTER),
        ("inst://rack/mainframe/source", "inst://rack/mainframe/source"),  # GPIB bus, then SIM900 slots
        ("inst://rack/mainframe/voltmeter/channel/3", "inst://rack/mainframe/voltmeter"),  # SIM970 declares nothing
    ],
)
def test_paths_widen_to_their_declared_claim_unit(path, unit, rig):
    assert _registry(rig).claim_unit_for(path) == unit


def test_claiming_a_voltmeter_channel_claims_the_whole_voltmeter(server: WireServer):
    result = server.claim_acquire(paths=["inst://rack/mainframe/voltmeter/channel/1"], holder="iv")
    assert result["units"] == ["inst://rack/mainframe/voltmeter"]


def test_an_unknown_path_is_refused_before_anything_is_claimed(server: WireServer):
    with pytest.raises(ValueError, match="No instrument"):
        server.claim_acquire(paths=[CH0, "inst://nothing"], holder="x")
    assert server.claim_list() == []


# --------------------------- enforcement ---------------------------


def test_an_unclaimed_instrument_is_open_to_anyone(server: WireServer):
    assert server.call(CH0, "set_threshold", [30.0]) is True


def test_a_claimed_channel_refuses_other_writers_but_answers_queries(server: WireServer):
    token = server.claim_acquire(paths=[CH0], holder="calibration")["token"]

    error = _denied(server.call, CH0, "set_threshold", [30.0])
    assert "claimed by calibration" in str(error)
    assert server.call(CH0, "get_gate_time") == pytest.approx(1.0)  # a query needs nothing
    assert server.call(CH0, "set_threshold", [30.0], token=token) is True
    assert server.call(CH1, "set_threshold", [30.0]) is True  # the sibling is free


def test_a_channel_holder_cannot_change_the_counters_shared_state(server: WireServer):
    """Even with nobody else holding anything: a trigger change would survive
    into whoever claims the other input next."""
    token = server.claim_acquire(paths=[CH0], holder="calibration")["token"]
    error = _denied(server.call, COUNTER, "configure_gate", kwargs={"source": "input2"}, token=token)
    assert "covers inst://counter/channel/0 but not inst://counter" in str(error)


def test_a_root_holder_may_write_anywhere_beneath_it(server: WireServer):
    token = server.claim_acquire(paths=[COUNTER], holder="gated run")["token"]
    assert server.call(COUNTER, "configure_gate", kwargs={"source": "input2"}, token=token) is True
    assert server.call(CH1, "set_threshold", [30.0], token=token) is True


def test_a_write_under_a_lost_claim_stops_the_run(server: WireServer, clock: Clock):
    token = server.claim_acquire(paths=[CH1], holder="slow run", ttl_s=5)["token"]
    clock.now += 10
    error = _denied(server.call, "inst://rack/mainframe/source", "set_voltage", [0.01], token=token)
    assert "no longer held" in str(error)


def test_renewal_of_an_expired_claim_is_refused(server: WireServer, clock: Clock):
    token = server.claim_acquire(paths=[CH1], holder="x", ttl_s=5)["token"]
    clock.now += 10
    _denied(server.claim_renew, token)


# --------------------------- release and baseline ---------------------------


def test_release_restores_the_released_units_to_baseline(server: WireServer):
    token = server.claim_acquire(paths=[CH0], holder="threshold sweep")["token"]
    server.call(CH0, "set_threshold", [400.0], token=token)

    assert server.claim_release(token) == {"released": [CH0]}

    channel = server._registry.resolve(CH0)
    assert channel.get_threshold() == pytest.approx(-50.0)
    assert server.claim_list() == []


def test_expiry_restores_baseline_too_and_is_recorded(server: WireServer, clock: Clock):
    token = server.claim_acquire(paths=[CH0], holder="crashed client", ttl_s=5)["token"]
    server.call(CH0, "set_threshold", [400.0], token=token)
    clock.now += 10

    assert server.claim_list() == []  # reaped, restored, freed
    assert server._registry.resolve(CH0).get_threshold() == pytest.approx(-50.0)
    kinds = [e["kind"] for e in server.events_recent()]
    assert "claim.expire" in kinds


def test_release_never_opens_hardware_to_reset_it(server: WireServer):
    token = server.claim_acquire(paths=["inst://rack/mainframe/source"], holder="x")["token"]
    server.claim_release(token)
    assert server.list_held()["held_paths"] == []


# --------------------------- refusals elsewhere ---------------------------


def test_a_claimed_rack_cannot_be_released_or_removed(server: WireServer):
    server.claim_acquire(paths=[CH0], holder="calibration")
    with pytest.raises(ValueError, match="calibration holds a claim"):
        server.release(COUNTER)
    with pytest.raises(ValueError, match="holds a claim"):
        server._refuse_if_claimed("counter", "remove")


def test_only_this_machine_may_force_release(server: WireServer):
    server.claim_acquire(paths=[CH0], holder="stuck")
    token = set_current_peer(Peer(transport="tcp", identity="ab"))
    try:
        with pytest.raises(LocalOnlyError):
            server.claim_force_release(COUNTER)
    finally:
        reset_current_peer(token)

    assert server.claim_force_release(COUNTER) == {"released": [CH0]}
    assert server.claim_list() == []


# --------------------------- which calls the gate serializes ---------------------------


def test_gate_involvement_is_limited_to_calls_a_rule_touches():
    from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928

    gate = PermissionGate(
        load_permissions(
            {
                "rules": [
                    {
                        "id": "no_pulse_while_biased",
                        "when": {"path": "inst://rack/mainframe/source", "key": "output", "equals": "on"},
                        "deny": [{"path": "inst://funcgen", "methods": ["pulse"]}],
                    }
                ]
            }
        )
    )
    assert gate.involves("inst://funcgen", "pulse", None)  # denied by a rule
    assert gate.involves("inst://rack/mainframe/source", "turn_on", Sim928)  # changes watched state
    assert not gate.involves("inst://rack/mainframe/other", "turn_on", Sim928)  # unwatched path
    assert not gate.involves("inst://rack/mainframe/source", "get_status", Sim928)  # not a state method
    assert not PermissionGate().involves("inst://funcgen", "pulse", None)  # no rules at all
