"""Phase C and D: safe tree writes, and custom resources across sources.

The tree-write path was the newest and least protected surface in the system —
it had no automated coverage at all, while being the one thing that can rebuild
the index under live hardware.
"""

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.server.peer import Peer, reset_current_peer, set_current_peer
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    load_instruments,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend import custom_resource_generation as crg
from lab_wizard.wizard.backend.custom_resource_generation import (
    CustomResourceSelection,
    GenerateCustomResourceRequest,
    generate_custom_resource_project,
)
from lab_wizard.wizard.backend.transport_status import duplicate_transport_check


_PROLOGIX_KEY = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
_SIM900_KEY = instrument_hash("sim900", "5")
_SIM928_KEY = instrument_hash("sim928", "1")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "servers"))
    monkeypatch.setenv("LAB_WIZARD_LEASE_DIR", str(tmp_path / "leases"))
    yield


def _write_config(config_dir: Path) -> dict:
    instruments = {
        _PROLOGIX_KEY: PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                _SIM900_KEY: Sim900Params(
                    gpib_address="5", children={_SIM928_KEY: Sim928Params(slot="1")}
                )
            },
        ),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)
    return load_instruments(config_dir)


def _server(config_dir: Path) -> WireServer:
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    return WireServer(
        bind=["ipc:///tmp/lw-test.sock"], registry=registry, config_dir=str(config_dir)
    )


@pytest.fixture
def local_peer():
    token = set_current_peer(Peer(transport="ipc", identity="test"))
    yield
    reset_current_peer(token)


# --------------------------- C4: the held-rack guard ---------------------------


def test_a_child_of_a_held_rack_cannot_be_removed(tmp_path, local_peer):
    """The bug this fixes: the guard tested ``inst://<key>`` directly, so it only
    ever matched a top-level key. Removing a *child* of an open rack — which
    rebuilds the index under live hardware just the same — sailed through."""
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    server = _server(config_dir)

    # Open the rack, as a call would.
    server._registry._index[f"inst://{_PROLOGIX_KEY}"] = object()

    with pytest.raises(ValueError, match="while its hardware is open"):
        server.tree_remove(type="sim928", key=_SIM928_KEY)


def test_adding_under_a_held_rack_is_refused(tmp_path, local_peer):
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    server = _server(config_dir)
    server._registry._index[f"inst://{_PROLOGIX_KEY}"] = object()

    chain = [
        {"type": "sim928", "key": "2", "action": "create_new", "extra": {}},
        {"type": "sim900", "key": _SIM900_KEY, "action": "use_existing", "extra": {}},
        {
            "type": "prologix_gpib",
            "key": _PROLOGIX_KEY,
            "action": "use_existing",
            "extra": {},
        },
    ]
    with pytest.raises(ValueError, match="while its hardware is open"):
        server.tree_add(chain=chain)


def test_a_free_rack_can_still_be_edited(tmp_path, local_peer):
    """The guard must not become a blanket refusal."""
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    server = _server(config_dir)

    result = server.tree_remove(type="sim928", key=_SIM928_KEY)
    assert result["status"] == "ok"
    assert not any("sim928" in p for p in server._registry.list_paths())


def test_the_reload_happens_under_every_transport_lock(tmp_path, local_peer):
    """Replacing the registry replaces its lock table, so a call on an unrelated
    root could end up holding a lock from the old table while later calls take
    one from the new — two locks for one bus."""
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    server = _server(config_dir)

    acquired: list[str] = []
    real_lock = server._registry.transport_lock

    class _Tracking:
        def __init__(self, root, lock):
            self.root, self.lock = root, lock

        def __enter__(self):
            acquired.append(self.root)
            return self.lock.__enter__()

        def __exit__(self, *a):
            return self.lock.__exit__(*a)

    server._registry.transport_lock = lambda p: _Tracking(p, real_lock(p))  # type: ignore[method-assign]
    server.tree_remove(type="sim928", key=_SIM928_KEY)
    assert f"inst://{_PROLOGIX_KEY}" in acquired


# --------------------------- C5: duplicate transports ---------------------------


def test_a_device_already_configured_locally_is_reported(tmp_path):
    config_dir = tmp_path / "config"
    _write_config(config_dir)

    result = duplicate_transport_check(
        "prologix_gpib", "/dev/ttyUSB0", config_dir=config_dir
    )
    assert result["transport_key"] == "serial:///dev/ttyUSB0"
    assert [c["root"] for c in result["clashes"]] == [f"inst://{_PROLOGIX_KEY}"]


def test_a_different_device_is_not_a_clash(tmp_path):
    config_dir = tmp_path / "config"
    _write_config(config_dir)

    result = duplicate_transport_check(
        "prologix_gpib", "/dev/ttyUSB9", config_dir=config_dir
    )
    assert result["clashes"] == []


def test_an_instrument_with_no_transport_of_its_own_never_clashes(tmp_path):
    """A child borrows its root's transport, so it has nothing to duplicate."""
    config_dir = tmp_path / "config"
    _write_config(config_dir)
    assert duplicate_transport_check("sim928", "1", config_dir=config_dir) == {
        "transport_key": None,
        "clashes": [],
    }


# --------------------------- D1: routed custom resources ---------------------------


_REMOTE_URL = "ipc:///tmp/lw-custom-test.sock"


@pytest.fixture
def fake_source(monkeypatch):
    offered = [
        {
            "attribute_name": "rack_vsource",
            "path": "inst://abc",
            "behavior_abc": "VSource",
            "type_hint": "Sim928",
        }
    ]
    monkeypatch.setattr(crg, "attributes_for_source", lambda cd, name: offered)
    monkeypatch.setattr(crg, "resolve_source_url", lambda cd, name: _REMOTE_URL)
    return offered


def _read_yaml(path: Path) -> dict:
    y = YAML(typ="rt")
    with path.open(encoding="utf-8") as f:
        return y.load(f)


def test_a_routed_custom_resource_records_a_source_and_no_params(tmp_path, fake_source):
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            selections=[
                CustomResourceSelection(
                    variable_name="bias", source="rack", attribute="rack_vsource"
                )
            ],
            file_style="simple",
        ),
    )

    data = _read_yaml(Path(out["yaml_file"]))
    assert data["resources"]["instruments"] == {}
    assert dict(data["resources"]["instrument_sources"]) == {"rack_vsource": "rack"}


def test_the_generated_file_builds_a_composite_and_resolves_by_attribute(
    tmp_path, fake_source
):
    """Savers and plotters stay local while instruments route — which is exactly
    what CompositeResources is for, and why a bare RemoteResources is wrong."""
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            selections=[
                CustomResourceSelection(
                    variable_name="bias", source="rack", attribute="rack_vsource"
                )
            ],
            file_style="simple",
        ),
    )

    code = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "CompositeResources.from_project(" in code
    assert "load_server_urls(" in code
    assert "resource_config.from_attribute('rack_vsource')" in code
    # Typed as the behavior ABC the source reported, not the driver class — the
    # runtime object is a proxy.
    assert "VSource" in code


def test_a_style_that_cannot_address_a_routed_instrument_is_upgraded(
    tmp_path, fake_source
):
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            selections=[
                CustomResourceSelection(
                    variable_name="bias", source="rack", attribute="rack_vsource"
                )
            ],
            file_style="simple",
            generation_style="production",  # deliberately the wrong one
        ),
    )
    code = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "from_attribute" in code
    assert ".from_config(resource_config, key=" not in code


def test_a_stale_routed_attribute_is_refused(tmp_path, fake_source):
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_config(config_dir)

    with pytest.raises(ValueError, match="no longer offers"):
        generate_custom_resource_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=GenerateCustomResourceRequest(
                selections=[
                    CustomResourceSelection(
                        variable_name="bias", source="rack", attribute="deleted"
                    )
                ],
                file_style="simple",
            ),
        )


def test_a_purely_local_custom_resource_is_unchanged(tmp_path):
    """No sources block, no composite — existing behaviour bit for bit."""
    from lab_wizard.wizard.backend._generation_common import SelectedNodeRef

    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    _write_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            selections=[
                CustomResourceSelection(
                    variable_name="bias",
                    type="sim928",
                    key=_SIM928_KEY,
                    path=[
                        SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                        SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                        SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                    ],
                )
            ],
            file_style="simple",
        ),
    )

    data = _read_yaml(Path(out["yaml_file"]))
    assert "instrument_sources" not in data["resources"]
    code = Path(out["setup_file"]).read_text(encoding="utf-8")
    assert "CompositeResources" not in code
    assert "resource_config = project.resources" in code
