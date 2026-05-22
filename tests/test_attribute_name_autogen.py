"""Tests for hybrid ``{type}-{petname}`` attribute-name autogeneration."""

from __future__ import annotations

from pydantic import BaseModel, Field

from lab_wizard.wizard.backend._generation_common import _NodeRef
from lab_wizard.wizard.backend.attribute_name_autogen import autogen_attribute_names


class _ChannelParams(BaseModel):
    attribute_name: str = ""


class _Params(BaseModel):
    type: str = "dac4D"
    attribute_name: str = ""
    channels: list[_ChannelParams] = Field(default_factory=list)
    children: dict = Field(default_factory=dict)


def _leaf(type_str: str, n_channels: int = 0) -> _NodeRef:
    params = _Params(
        type=type_str,
        channels=[_ChannelParams() for _ in range(n_channels)],
    )
    return _NodeRef(key="k", params=params, parent=None)


def _seq_petnames(*names: str):
    """A deterministic petname generator yielding the given names in order."""
    it = iter(names)
    return lambda: next(it)


def test_hybrid_type_petname_for_node():
    leaf = _leaf("dac4D")
    muts = autogen_attribute_names(
        {}, [(leaf, None)], petname_fn=_seq_petnames("vanilla-seafoam")
    )
    assert len(muts) == 1
    assert muts[0].chosen_name == "dac4d-vanilla-seafoam"
    assert leaf.params.attribute_name == "dac4d-vanilla-seafoam"


def test_hybrid_for_channels_drops_positional_suffix():
    leaf = _leaf("dac4D", n_channels=2)
    muts = autogen_attribute_names(
        {},
        [(leaf, 0), (leaf, 1)],
        petname_fn=_seq_petnames("alpha-one", "beta-two"),
    )
    names = {m.chosen_name for m in muts}
    assert names == {"dac4d-alpha-one", "dac4d-beta-two"}
    assert leaf.params.channels[0].attribute_name == "dac4d-alpha-one"
    assert leaf.params.channels[1].attribute_name == "dac4d-beta-two"


def test_existing_names_are_left_untouched():
    leaf = _leaf("dac4D")
    leaf.params.attribute_name = "cryo_bias_source"
    muts = autogen_attribute_names(
        {}, [(leaf, None)], petname_fn=_seq_petnames("should-not-be-used")
    )
    assert muts == []
    assert leaf.params.attribute_name == "cryo_bias_source"


def test_collision_retries_a_fresh_petname():
    # Pretend the tree already contains the first petname; generator must retry.
    leaf = _leaf("dac4D")
    existing = {
        "type": "dac4D",
        "attribute_name": "dac4d-taken-name",
        "channels": [],
        "children": {},
    }
    instruments = {"k": _Params(type="dac4D", attribute_name="dac4d-taken-name")}
    muts = autogen_attribute_names(
        instruments,
        [(leaf, None)],
        petname_fn=_seq_petnames("taken-name", "fresh-name"),
    )
    assert muts[0].chosen_name == "dac4d-fresh-name"
