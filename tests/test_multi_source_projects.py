"""Phase B: authoring a measurement whose instruments live in several places.

The satellite case is the one that matters. A cloned workspace has an empty
tree, so a picker that only ever showed the local tree left nothing to pick and
the whole cross-workspace story stopped at "you can look at it".

What a routed selection has to get right:

* the project YAML must **not** carry a copy of the routed instrument's params —
  the server owns that config, and a second copy is a second thing to drift;
* it must carry ``instrument_sources`` instead, which is what makes
  ``CompositeResources`` route at run time;
* generation must fall back to ``from_attribute`` style, because the other
  styles address a params tree this workspace does not have;
* and two sources must not both claim one attribute name, since routing is keyed
  on exactly that.
"""

from pathlib import Path

import pytest
from ruamel.yaml import YAML

from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    load_instruments,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend import instrument_sources as sources_mod
from lab_wizard.wizard.backend.instrument_sources import ensure_source_registered
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)
from lab_wizard.wizard.backend.remote_servers import load_remote_servers


_PROLOGIX_KEY = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
_SIM900_KEY = instrument_hash("sim900", "5")
_SIM928_KEY = instrument_hash("sim928", "1")
_SIM970_KEY = instrument_hash("sim970", "2")
_KEYSIGHT_KEY = instrument_hash("keysight53220A", "10.0.0.5:5025")

_REMOTE_SOURCE = "master"
_REMOTE_URL = "ipc:///tmp/lab_wizard-test.sock"


def _write_test_config(config_dir: Path) -> dict:
    instruments = {
        _PROLOGIX_KEY: PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                _SIM900_KEY: Sim900Params(
                    gpib_address="5",
                    children={
                        _SIM928_KEY: Sim928Params(slot="1"),
                        _SIM970_KEY: Sim970Params(slot="2"),
                    },
                )
            },
        ),
        _KEYSIGHT_KEY: Keysight53220AParams(ip_address="10.0.0.5", ip_port=5025),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)
    return load_instruments(config_dir)


def _attribute_of(instruments: dict, *keys: str, channel: int | None = None) -> str:
    node = instruments[keys[0]]
    for key in keys[1:]:
        node = node.children[key]
    if channel is not None:
        return node.channels[channel].attribute_name
    return node.attribute_name


@pytest.fixture
def workspace(tmp_path: Path):
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    config_dir.mkdir(parents=True)
    projects_dir.mkdir(parents=True)
    instruments = _write_test_config(config_dir)
    return config_dir, projects_dir, instruments


@pytest.fixture
def fake_source(monkeypatch):
    """A reachable source offering one named leaf, without a live server."""
    offered = [
        {
            "attribute_name": "master_vsource",
            "path": "inst://abc123",
            "behavior_abc": "VSource",
            "type_hint": "Sim928",
        }
    ]
    monkeypatch.setattr(
        sources_mod, "attributes_for_source", lambda config_dir, name: offered
    )
    monkeypatch.setattr(
        sources_mod, "resolve_source_url", lambda config_dir, name: _REMOTE_URL
    )
    import lab_wizard.wizard.backend.project_generation as pg

    monkeypatch.setattr(pg, "attributes_for_source", lambda config_dir, name: offered)
    monkeypatch.setattr(pg, "resolve_source_url", lambda config_dir, name: _REMOTE_URL)
    return offered


def _local_sense(instruments) -> SelectedResource:
    return SelectedResource(
        variable_name="voltage_sense",
        type="sim970",
        key=_SIM970_KEY,
        path=[
            SelectedNodeRef(type="sim970", key=_SIM970_KEY),
            SelectedNodeRef(type="sim900", key=_SIM900_KEY),
            SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
        ],
        channel_index=0,
    )


def _routed_source() -> SelectedResource:
    return SelectedResource(
        variable_name="voltage_source",
        source=_REMOTE_SOURCE,
        attribute="master_vsource",
    )


def _read_yaml(path: Path) -> dict:
    y = YAML(typ="rt")
    with path.open(encoding="utf-8") as f:
        return y.load(f)


# --------------------------- mixed-source projects ---------------------------


def test_a_routed_instrument_is_recorded_as_a_source_not_copied(
    workspace, fake_source
):
    """The server owns that config; a copy here would be a copy to drift."""
    config_dir, projects_dir, instruments = workspace

    result = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            selected_resources=[_routed_source(), _local_sense(instruments)],
        ),
    )

    data = _read_yaml(Path(result["yaml_file"]))
    resources = data["resources"]

    # Only the local rack's params were copied.
    assert set(resources["instruments"].keys()) == {_PROLOGIX_KEY}

    sense_attr = _attribute_of(
        instruments, _PROLOGIX_KEY, _SIM900_KEY, _SIM970_KEY, channel=0
    )
    assert dict(resources["instrument_sources"]) == {
        "master_vsource": _REMOTE_SOURCE,
        sense_attr: "local",
    }


def test_a_mixed_project_is_generated_in_from_attribute_style(workspace, fake_source):
    """Other styles emit ``from_config(resources, key=<hash>)``, which addresses
    a params tree this workspace does not have for a routed instrument."""
    config_dir, projects_dir, instruments = workspace

    result = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            selected_resources=[_routed_source(), _local_sense(instruments)],
            generation_style="production",  # deliberately the wrong one
        ),
    )

    setup = Path(result["setup_file"]).read_text(encoding="utf-8")
    assert "resources.from_attribute('master_vsource')" in setup
    assert ".from_config(resources, key=" not in setup


def test_the_source_is_registered_so_the_project_can_resolve_it(
    workspace, fake_source
):
    """A project records names and resolves them through the address book, so
    the user never has to type a URL for a server the wizard discovered."""
    config_dir, projects_dir, instruments = workspace

    generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            selected_resources=[_routed_source(), _local_sense(instruments)],
        ),
    )

    assert {s["name"]: s["url"] for s in load_remote_servers(config_dir)} == {
        _REMOTE_SOURCE: _REMOTE_URL
    }


# --------------------------- validation ---------------------------


def test_an_attribute_the_source_no_longer_offers_is_refused(workspace, fake_source):
    """The picker may have been open while that workspace was edited."""
    config_dir, projects_dir, instruments = workspace

    stale = SelectedResource(
        variable_name="voltage_source",
        source=_REMOTE_SOURCE,
        attribute="deleted_instrument",
    )
    with pytest.raises(ValueError, match="no longer offers"):
        generate_measurement_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=GenerateProjectRequest(
                measurement_name="iv_curve",
                selected_resources=[stale, _local_sense(instruments)],
            ),
        )


def test_a_routed_selection_without_an_attribute_is_refused(workspace, fake_source):
    config_dir, projects_dir, instruments = workspace

    nameless = SelectedResource(variable_name="voltage_source", source=_REMOTE_SOURCE)
    with pytest.raises(ValueError, match="carries no attribute name"):
        generate_measurement_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=GenerateProjectRequest(
                measurement_name="iv_curve",
                selected_resources=[nameless, _local_sense(instruments)],
            ),
        )


def test_two_sources_claiming_one_attribute_name_are_refused(
    workspace, monkeypatch
):
    """Routing is keyed on attribute name, so the second entry would silently
    shadow the first — and the user would never learn which one ran."""
    config_dir, projects_dir, instruments = workspace

    clashing = _attribute_of(
        instruments, _PROLOGIX_KEY, _SIM900_KEY, _SIM970_KEY, channel=0
    )
    import lab_wizard.wizard.backend.project_generation as pg

    monkeypatch.setattr(
        pg,
        "attributes_for_source",
        lambda config_dir, name: [
            {"attribute_name": clashing, "behavior_abc": "VSource", "type_hint": "X"}
        ],
    )
    monkeypatch.setattr(pg, "resolve_source_url", lambda config_dir, name: _REMOTE_URL)

    routed = SelectedResource(
        variable_name="voltage_source", source=_REMOTE_SOURCE, attribute=clashing
    )
    with pytest.raises(ValueError, match="both provide an instrument named"):
        generate_measurement_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=GenerateProjectRequest(
                measurement_name="iv_curve",
                selected_resources=[routed, _local_sense(instruments)],
            ),
        )


# --------------------------- local projects are untouched ---------------------------


def test_a_purely_local_project_emits_no_sources_and_keeps_its_style(workspace):
    """Existing behaviour must be bit-for-bit unchanged when nothing is routed."""
    config_dir, projects_dir, instruments = workspace

    local_source = SelectedResource(
        variable_name="voltage_source",
        type="sim928",
        key=_SIM928_KEY,
        path=[
            SelectedNodeRef(type="sim928", key=_SIM928_KEY),
            SelectedNodeRef(type="sim900", key=_SIM900_KEY),
            SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
        ],
        # sim928 is single-channel; production style validates that.
    )
    result = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            selected_resources=[local_source, _local_sense(instruments)],
            generation_style="production",
        ),
    )

    data = _read_yaml(Path(result["yaml_file"]))
    assert "instrument_sources" not in data["resources"]
    setup = Path(result["setup_file"]).read_text(encoding="utf-8")
    assert ".from_config(resources, key=" in setup
    assert "from_attribute" not in setup


# --------------------------- address book ---------------------------


def test_registering_a_source_is_idempotent(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    assert ensure_source_registered(config_dir, "rack", "ipc://a") == "rack"
    assert ensure_source_registered(config_dir, "rack", "ipc://a") == "rack"
    assert len(load_remote_servers(config_dir)) == 1


def test_a_daemon_on_this_machine_is_not_also_listed_as_remote(tmp_path, monkeypatch):
    """Generation registers a same-machine daemon in the address book so a
    project can resolve its name — which must not make it appear twice, once
    with a tree and edit rights and once as a read-only copy of itself.
    """
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "registry"))
    config_dir = tmp_path / "satellite" / "config"
    (config_dir / "instruments").mkdir(parents=True)

    from lab_wizard.lib.client.server_registry import advertise_server

    other = tmp_path / "master" / "config"
    other.mkdir(parents=True)
    advertise_server(other, bind="tcp://0.0.0.0:12300", ipc="ipc:///tmp/master.sock")

    # Both spellings of the same server, as generation would record them.
    ensure_source_registered(config_dir, "master", "ipc:///tmp/master.sock")
    ensure_source_registered(config_dir, "master-tcp", "tcp://127.0.0.1:12300")
    ensure_source_registered(config_dir, "cryo-rack", "tcp://10.9.9.9:12300")

    kinds = [
        (s["name"], s["kind"])
        for s in sources_mod.list_instrument_sources(str(config_dir))["sources"]
    ]
    assert ("master", "machine") in kinds
    # Neither the ipc nor the wildcard-bind spelling leaks into the remote list.
    assert [k for k in kinds if k[1] == "remote"] == [("cryo-rack", "remote")]


def test_a_name_already_pointing_elsewhere_is_not_rewritten(tmp_path):
    """Another project may depend on that entry, so allocate a new name instead."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    ensure_source_registered(config_dir, "rack", "ipc://a")

    assert ensure_source_registered(config_dir, "rack", "ipc://b") == "rack-2"
    registered = {s["name"]: s["url"] for s in load_remote_servers(config_dir)}
    assert registered == {"rack": "ipc://a", "rack-2": "ipc://b"}
