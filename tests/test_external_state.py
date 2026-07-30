"""Phase 3: state owned by an external authority is read, not inferred."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.dbay.state_sync import ChannelState, channel_states
from lab_wizard.lib.server.external_state import ExternalStateBridge, slot_channel_paths
from lab_wizard.lib.server.permissions import StateTracker
from lab_wizard.lib.server.registry import InstrumentRegistry


# --------------------------- the declarative map ---------------------------


def _snapshot(bias=1.5, activated=True, slot=1):
    return {
        "data": [
            {
                "module_type": "dac4D",
                "core": {"slot": slot, "type": "dac4D", "name": "D"},
                "vsource": {
                    "channels": [
                        {
                            "index": 0,
                            "bias_voltage": bias,
                            "activated": activated,
                            "heading_text": "",
                            "measuring": False,
                        },
                        {
                            "index": 1,
                            "bias_voltage": 0.0,
                            "activated": False,
                            "heading_text": "",
                            "measuring": False,
                        },
                    ]
                },
            }
        ]
    }


def test_vsource_channels_map_to_voltage_and_output():
    states = list(channel_states(_snapshot()))
    assert ChannelState(1, "vsource", 0, "voltage", 1.5) in states
    assert ChannelState(1, "vsource", 0, "output", "on") in states
    assert ChannelState(1, "vsource", 1, "output", "off") in states


def test_activated_becomes_the_gates_on_off_vocabulary():
    """Rules speak "on"/"off"; DBay reports a bool. The map converts.

    Worth noting this is state inference could never supply: Dac4DChannel has no
    separate output enable, so it never records "output" at all.
    """
    assert ChannelState(1, "vsource", 0, "output", "on") in list(
        channel_states(_snapshot(activated=True))
    )
    assert ChannelState(1, "vsource", 0, "output", "off") in list(
        channel_states(_snapshot(activated=False))
    )


def test_singleton_addons_are_addressed_as_channel_zero():
    """dac16D's shared bias and reference read are one-channel addons."""
    snapshot = {
        "data": [
            {
                "module_type": "dac16D",
                "core": {"slot": 2, "type": "dac16D", "name": "D16"},
                "vsb": {
                    "index": 0,
                    "bias_voltage": 3.0,
                    "activated": True,
                    "heading_text": "",
                    "measuring": False,
                },
                "vr": {"index": 0, "voltage": 0.25, "measuring": True, "name": "vr"},
            }
        ]
    }
    states = list(channel_states(snapshot))
    assert ChannelState(2, "vsource", 0, "voltage", 3.0) in states
    assert ChannelState(2, "vsource", 0, "output", "on") in states
    assert ChannelState(2, "vsense", 0, "voltage", 0.25) in states


def test_unknown_module_contributes_nothing_rather_than_failing():
    """A rack may hold modules newer than this build."""
    snapshot = {
        "data": [
            {
                "module_type": "fafd",
                "core": {"slot": 0, "type": "fafd", "name": "F"},
                "mystery": [1, 2],
            },
            _snapshot(slot=1)["data"][0],
        ]
    }
    slots = {s.slot for s in channel_states(snapshot)}
    assert slots == {1}


def test_accepts_validated_models_not_only_dicts():
    """Works with or without the dbay schema in hand."""
    from dbay.state import SystemState

    payload = {**_snapshot(), "valid": True, "dev_mode": False}
    states = list(channel_states(SystemState.model_validate(payload)))
    assert ChannelState(1, "vsource", 0, "voltage", 1.5) in states


# --------------------------- tracker semantics ---------------------------


class _Inst:
    def __init__(self):
        self.children = {}

    def make_child(self, key):
        return _Inst()


class _Child(BaseModel):
    type: Literal["dac4D"] = "dac4D"
    slot: str = "1"
    channels: dict = Field(default_factory=dict)


class _DBayRoot(BaseModel):
    type: Literal["dbay"] = "dbay"
    children: dict[str, _Child] = Field(default_factory=dict)

    @property
    def inst(self):
        return _Inst

    def create_inst(self):
        return _Inst()

    def transport_sharing(self):
        return "shared"

    def state_authority(self):
        return "subscribed"

    def transport_key(self):
        return "dbay-gui://127.0.0.1:8345"


class _FakeAuthority:
    """Stands in for DBayClient: a snapshot, a version, and callbacks."""

    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.state_version = 1
        self.client_id = "me"
        self._patch_cbs = []
        self._snapshot_cbs = []
        self.snapshot_calls = 0

    def snapshot(self):
        self.snapshot_calls += 1
        return self._snapshot

    def on_patch(self, cb):
        self._patch_cbs.append(cb)
        return lambda: self._patch_cbs.remove(cb)

    def on_snapshot(self, cb):
        self._snapshot_cbs.append(cb)
        return lambda: self._snapshot_cbs.remove(cb)

    def emit_patch(self, snapshot, version, origin=None):
        self._snapshot = snapshot
        self.state_version = version
        event = type(
            "PatchEvent", (), {"version": version, "origin_client_id": origin}
        )()
        for cb in list(self._patch_cbs):
            cb(event)

    def emit_snapshot(self, snapshot, version):
        self._snapshot = snapshot
        self.state_version = version
        event = type("SnapshotEvent", (), {"version": version})()
        for cb in list(self._snapshot_cbs):
            cb(event)


def _bridge(snapshot):
    # num_channels drives dense channel registration in the lazy builder; a
    # plain params object without it registers no channels, so paths are built
    # from an explicit channels mapping instead.
    class Chan(BaseModel):
        attribute_name: str = ""

    child = _Child(channels={0: Chan(), 1: Chan()})
    type(child).num_channels = 2  # type: ignore[attr-defined]

    registry = InstrumentRegistry.from_instruments(
        {"dbay1": _DBayRoot(children={"mod1": child})}
    )
    tracker = StateTracker()
    authority = _FakeAuthority(snapshot)
    bridge = ExternalStateBridge(
        registry, tracker, "inst://dbay1", client_factory=lambda: authority
    )
    return registry, tracker, authority, bridge


def test_slot_channel_paths_joins_slots_to_paths():
    registry, _, _, _ = _bridge(_snapshot())
    paths = slot_channel_paths(registry, "inst://dbay1")
    assert paths[(1, 0)] == "inst://dbay1/mod1/channel/0"
    assert paths[(1, 1)] == "inst://dbay1/mod1/channel/1"


def test_subscription_seeds_state_from_the_authority():
    _, tracker, _, bridge = _bridge(_snapshot(bias=2.25, activated=True))
    assert bridge.start() is True
    path = "inst://dbay1/mod1/channel/0"
    assert tracker.get(path, "voltage") == 2.25
    assert tracker.get(path, "output") == "on"


def test_record_is_a_no_op_for_subscribed_paths():
    """Inferring state you are being told is a correctness bug.

    A clamped setpoint is the concrete failure: we send 10 V, the hardware
    takes 5, and recording our own value would make the gate believe 10.
    """
    _, tracker, _, bridge = _bridge(_snapshot(bias=1.0))
    bridge.start()
    path = "inst://dbay1/mod1/channel/0"

    class Chan:
        _state_methods_ = {"set_voltage": ("voltage", 99.0)}

    tracker.record(path, Chan(), "set_voltage", [99.0], {}, True)
    assert tracker.get(path, "voltage") == 1.0  # authority still wins


def test_unsubscribed_paths_still_infer():
    _, tracker, _, bridge = _bridge(_snapshot())
    bridge.start()

    class Chan:
        _state_methods_ = {"set_voltage": ("voltage", 7.0)}

    tracker.record("inst://other/channel/0", Chan(), "set_voltage", [7.0], {}, True)
    assert tracker.get("inst://other/channel/0", "voltage") == 7.0


def test_patch_updates_state():
    _, tracker, authority, bridge = _bridge(_snapshot(bias=1.0))
    bridge.start()
    path = "inst://dbay1/mod1/channel/0"

    authority.emit_patch(_snapshot(bias=4.0, activated=False), version=2)
    assert tracker.get(path, "voltage") == 4.0
    assert tracker.get(path, "output") == "off"


def test_our_own_echo_is_ignored():
    _, tracker, authority, bridge = _bridge(_snapshot(bias=1.0))
    bridge.start()
    before = authority.snapshot_calls

    authority.emit_patch(_snapshot(bias=9.0), version=2, origin="me")
    # Neither applied nor even read — the callback returns immediately.
    assert tracker.get("inst://dbay1/mod1/channel/0", "voltage") == 1.0
    assert authority.snapshot_calls == before


def test_a_version_gap_forces_a_full_resync():
    """A missed update makes patch-derived state untrustworthy."""
    _, tracker, authority, bridge = _bridge(_snapshot(bias=1.0))
    bridge.start()
    calls_after_seed = authority.snapshot_calls

    authority.emit_patch(_snapshot(bias=3.0), version=9)  # jumped from 1
    assert authority.snapshot_calls > calls_after_seed
    assert tracker.get("inst://dbay1/mod1/channel/0", "voltage") == 3.0


def test_resync_clears_before_seeding():
    """Values absent from the new snapshot must read as unknown, not stale."""
    _, tracker, authority, bridge = _bridge(_snapshot(bias=1.0))
    bridge.start()
    path = "inst://dbay1/mod1/channel/0"
    assert tracker.get(path, "voltage") == 1.0

    # Authority comes back reporting an empty rack.
    authority.emit_snapshot({"data": []}, version=5)
    assert tracker.get(path, "voltage") is None
    assert tracker.get(path, "output") is None


def test_start_returns_false_when_the_authority_is_unreachable():
    """The server must still serve; it just cannot know this rack's state."""
    registry = InstrumentRegistry.from_instruments({"dbay1": _DBayRoot()})
    tracker = StateTracker()

    def boom():
        raise ConnectionError("no server")

    bridge = ExternalStateBridge(
        registry, tracker, "inst://dbay1", client_factory=boom
    )
    assert bridge.start() is False


def test_channels_absent_from_config_are_skipped():
    """Nothing addresses them, so no rule can reference them."""
    _, tracker, authority, bridge = _bridge(_snapshot(slot=7))
    bridge.start()
    assert tracker.snapshot() == {}
