"""Tests for the server hosting the config/instruments tree lazily.

Verifies that ``InstrumentRegistry.from_instruments`` / ``from_config_dir``:
  * index every node and named attribute without instantiating hardware,
  * derive behavior_abc / type_hint statically from params' instrument classes,
  * instantiate the live object only on first ``resolve`` and cache it.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, Field

from lab_wizard.lib.instruments.general.parent_child import ChannelProvider
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import (
    instrument_hash,
    save_instruments_to_config,
)

# A record of every create_inst / make_child call, to prove laziness.
_CALLS: list[str] = []


class _FakeChannel(VSource):
    def __init__(self) -> None:
        self._v = 0.0

    def set_voltage(self, voltage: float) -> bool:  # type: ignore[override]
        self._v = voltage
        return True

    def turn_on(self) -> bool:  # type: ignore[override]
        return True

    def turn_off(self) -> bool:  # type: ignore[override]
        return True

    def disconnect(self) -> bool:  # type: ignore[override]
        return True


class _FakeLeaf(ChannelProvider[_FakeChannel]):
    channel_class = _FakeChannel

    def __init__(self, n_channels: int) -> None:
        _CALLS.append("leaf")
        self.channels = [_FakeChannel() for _ in range(n_channels)]

    def make_child(self, key: str) -> Any:  # pragma: no cover - no grandchildren
        raise KeyError(key)


class _FakeRoot:
    def __init__(self, children: dict[str, Any]) -> None:
        _CALLS.append("root")
        self._children = children

    def make_child(self, key: str) -> Any:
        _CALLS.append(f"make_child:{key}")
        return _FakeLeaf(type(self._children[key]).num_channels)


class _ChannelParams(BaseModel):
    attribute_name: str = ""


class _LeafParams(BaseModel):
    attribute_name: str = ""
    num_channels: ClassVar[int] = 2
    channels: dict[int, _ChannelParams] = Field(default_factory=dict)

    @property
    def inst(self) -> type:
        return _FakeLeaf


class _RootParams(BaseModel):
    attribute_name: str = ""
    children: dict[str, _LeafParams] = Field(default_factory=dict)

    @property
    def inst(self) -> type:
        return _FakeRoot

    def create_inst(self) -> _FakeRoot:
        return _FakeRoot(self.children)


def _tree() -> dict[str, Any]:
    return {
        "root1": _RootParams(
            children={
                "leafA": _LeafParams(
                    channels={0: _ChannelParams(attribute_name="bias")}
                )
            }
        )
    }


def test_index_built_without_instantiation():
    _CALLS.clear()
    reg = InstrumentRegistry.from_instruments(_tree())

    assert reg.list_paths() == [
        "inst://root1",
        "inst://root1/leafA",
        "inst://root1/leafA/channel/0",
        "inst://root1/leafA/channel/1",
    ]
    # The named channel is indexed by attribute_name.
    assert reg.list_attributes() == {"bias": "inst://root1/leafA/channel/0"}
    # Nothing was instantiated just to build the index.
    assert _CALLS == []


def test_static_descriptions_no_hardware():
    _CALLS.clear()
    reg = InstrumentRegistry.from_instruments(_tree())

    assert reg.describe_path("inst://root1/leafA")["behavior_abc"] == "ChannelProvider"
    chan = reg.describe_attribute("bias")
    assert chan["behavior_abc"] == "VSource"
    assert chan["type_hint"] == "_FakeChannel"
    assert chan["attribute_name"] == "bias"
    # describe_* must not open hardware.
    assert _CALLS == []


def test_resolve_instantiates_lazily_and_caches():
    _CALLS.clear()
    reg = InstrumentRegistry.from_instruments(_tree())

    chan = reg.resolve("inst://root1/leafA/channel/0")
    assert isinstance(chan, _FakeChannel)
    # Resolving the channel built the root and the leaf exactly once each.
    assert _CALLS == ["root", "make_child:leafA", "leaf"]

    # Second resolve of the parent reuses the cached object — no new calls.
    reg.resolve("inst://root1/leafA")
    assert _CALLS == ["root", "make_child:leafA", "leaf"]


def test_from_config_dir_indexes_real_tree(tmp_path):
    config_dir = tmp_path / "config"
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
        config_dir,
    )
    reg = InstrumentRegistry.from_config_dir(str(config_dir))
    paths = reg.list_paths()
    path = f"inst://{root_key}/{rack_key}/{leaf_key}"
    assert path in paths
    assert reg.describe_path(path)["behavior_abc"] == "VSource"
