"""Tests for the manage-instruments CRUD API (add / reset / remove / tree).

These tests exercise the same functions called by the backend endpoints so
the behaviour seen through the GUI is covered without spinning up HTTP.
"""
import pathlib
import pytest

from lab_wizard.lib.utilities.config_io import (
    instrument_hash,
    add_instrument_chain,
    reinitialize_instrument,
    remove_instrument,
    get_configured_tree,
    load_instruments,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chain(*steps):
    """Shorthand for building a chain list (leaf-first)."""
    return list(steps)


def _step(type_str, key, action="create_new"):
    return {"type": type_str, "key": key, "action": action}


# ---------------------------------------------------------------------------
# Adding instruments
# ---------------------------------------------------------------------------

def test_add_top_level_usb_instrument(tmp_path):
    """Adding a prologix_gpib creates a hash-named YAML with the correct port field."""
    cfg = tmp_path / "config"
    inst_dir = cfg / "instruments"

    chain = _chain(_step("prologix_gpib", "/dev/ttyUSB0"))
    add_instrument_chain(cfg, chain)

    instruments = load_instruments(cfg)
    expected_key = instrument_hash("prologix_gpib", "/dev/ttyUSB0")

    assert expected_key in instruments
    params = instruments[expected_key]
    assert params.port == "/dev/ttyUSB0"

    # YAML file should have a hash-based name (no slashes!)
    yml_files = list(inst_dir.glob("*.yml"))
    assert len(yml_files) == 1
    assert yml_files[0].name == f"prologix_gpib_key_{expected_key}.yml"


def test_add_full_chain_prologix_sim900_sim928(tmp_path):
    """Adding a sim928 with new parent chain creates all three nodes correctly."""
    cfg = tmp_path / "config"

    # Chain is leaf-first: sim928 → sim900 → prologix_gpib
    chain = _chain(
        _step("sim928", "2"),
        _step("sim900", "7"),
        _step("prologix_gpib", "/dev/ttyUSB5"),
    )
    add_instrument_chain(cfg, chain)

    instruments = load_instruments(cfg)

    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB5")
    sim900_hash = instrument_hash("sim900", "7")
    sim928_hash = instrument_hash("sim928", "2")

    assert prologix_hash in instruments
    prologix = instruments[prologix_hash]
    assert prologix.port == "/dev/ttyUSB5"

    assert sim900_hash in prologix.children
    sim900 = prologix.children[sim900_hash]
    assert sim900.gpib_address == "7"

    assert sim928_hash in sim900.children
    sim928 = sim900.children[sim928_hash]
    assert sim928.slot == "2"


def test_add_child_to_existing_parent(tmp_path):
    """Adding a second sim928 to an existing sim900 uses use_existing correctly."""
    cfg = tmp_path / "config"

    # First: add prologix + sim900 + sim928 slot=1
    add_instrument_chain(cfg, _chain(
        _step("sim928", "1"),
        _step("sim900", "4"),
        _step("prologix_gpib", "/dev/ttyUSB0"),
    ))

    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    sim900_hash = instrument_hash("sim900", "4")

    # Second: add sim928 slot=3 using existing prologix and sim900
    add_instrument_chain(cfg, _chain(
        _step("sim928", "3"),
        _step("sim900", sim900_hash, action="use_existing"),
        _step("prologix_gpib", prologix_hash, action="use_existing"),
    ))

    instruments = load_instruments(cfg)
    sim900 = instruments[prologix_hash].children[sim900_hash]

    sim928_hash_1 = instrument_hash("sim928", "1")
    sim928_hash_3 = instrument_hash("sim928", "3")

    assert sim928_hash_1 in sim900.children
    assert sim928_hash_3 in sim900.children
    assert sim900.children[sim928_hash_1].slot == "1"
    assert sim900.children[sim928_hash_3].slot == "3"


# ---------------------------------------------------------------------------
# get_configured_tree
# ---------------------------------------------------------------------------

def test_get_configured_tree_structure(tmp_path):
    """get_configured_tree returns a JSON-friendly dict with type, key, children."""
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(
        _step("sim928", "2"),
        _step("sim900", "7"),
        _step("prologix_gpib", "/dev/ttyUSB0"),
    ))

    tree = get_configured_tree(cfg)
    assert isinstance(tree, list)
    assert len(tree) == 1

    prologix_node = tree[0]
    assert prologix_node["type"] == "prologix_gpib"
    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    assert prologix_node["key"] == prologix_hash

    sim900_hash = instrument_hash("sim900", "7")
    assert sim900_hash in prologix_node["children"]

    sim928_hash = instrument_hash("sim928", "2")
    sim900_node = prologix_node["children"][sim900_hash]
    assert sim928_hash in sim900_node["children"]


def test_get_configured_tree_fields(tmp_path):
    """Tree nodes include a 'fields' dict with the non-children params."""
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(_step("prologix_gpib", "/dev/ttyUSB3")))

    tree = get_configured_tree(cfg)
    fields = tree[0]["fields"]
    assert fields["port"] == "/dev/ttyUSB3"
    assert "children" not in fields


# ---------------------------------------------------------------------------
# reinitialize_instrument
# ---------------------------------------------------------------------------

def test_reinitialize_preserves_children(tmp_path):
    """Resetting a sim900 restores default fields but keeps its sim928 children."""
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(
        _step("sim928", "1"),
        _step("sim900", "4"),
        _step("prologix_gpib", "/dev/ttyUSB0"),
    ))

    sim900_hash = instrument_hash("sim900", "4")
    reinitialize_instrument(cfg, "sim900", sim900_hash)

    instruments = load_instruments(cfg)
    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    sim900 = instruments[prologix_hash].children[sim900_hash]

    sim928_hash = instrument_hash("sim928", "1")
    assert sim928_hash in sim900.children, "Children should survive a reset"


def test_reinitialize_nonexistent_raises(tmp_path):
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(_step("prologix_gpib", "/dev/ttyUSB0")))
    with pytest.raises(ValueError, match="not found"):
        reinitialize_instrument(cfg, "prologix_gpib", "deadbeef")


# ---------------------------------------------------------------------------
# remove_instrument
# ---------------------------------------------------------------------------

def test_remove_top_level(tmp_path):
    """Removing a top-level instrument deletes it from config."""
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(_step("prologix_gpib", "/dev/ttyUSB0")))

    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    remove_instrument(cfg, "prologix_gpib", prologix_hash)

    instruments = load_instruments(cfg)
    assert prologix_hash not in instruments


def test_remove_child(tmp_path):
    """Removing a child leaves the parent and sibling intact."""
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(
        _step("sim928", "1"),
        _step("sim900", "4"),
        _step("prologix_gpib", "/dev/ttyUSB0"),
    ))
    add_instrument_chain(cfg, _chain(
        _step("sim928", "3"),
        _step("sim900", instrument_hash("sim900", "4"), action="use_existing"),
        _step("prologix_gpib", instrument_hash("prologix_gpib", "/dev/ttyUSB0"), action="use_existing"),
    ))

    sim928_hash_1 = instrument_hash("sim928", "1")
    sim928_hash_3 = instrument_hash("sim928", "3")
    remove_instrument(cfg, "sim928", sim928_hash_1)

    instruments = load_instruments(cfg)
    prologix_hash = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
    sim900_hash = instrument_hash("sim900", "4")
    children = instruments[prologix_hash].children[sim900_hash].children

    assert sim928_hash_1 not in children
    assert sim928_hash_3 in children


def test_remove_nonexistent_raises(tmp_path):
    cfg = tmp_path / "config"
    add_instrument_chain(cfg, _chain(_step("prologix_gpib", "/dev/ttyUSB0")))
    with pytest.raises(ValueError, match="not found"):
        remove_instrument(cfg, "sim928", "deadbeef")
