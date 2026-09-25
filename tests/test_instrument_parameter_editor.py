"""The inspector edits one YAML node without losing siblings or stale changes."""

from copy import deepcopy
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lab_wizard.lib.server.peer import (
    Peer,
    LocalOnlyError,
    reset_current_peer,
    set_current_peer,
)
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer, HARDWARE_RPC
from lab_wizard.lib.utilities.config_io import (
    add_instrument_chain,
    get_configured_tree,
    instrument_hash,
    update_instrument_params,
    _read_yaml,
    _write_yaml,
)
from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata


def step(type, key, action="create_new"):
    return {"type": type, "key": key, "action": action}


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "servers"))
    monkeypatch.setenv("LAB_WIZARD_LEASE_DIR", str(tmp_path / "leases"))
    cfg = tmp_path / "config"
    for port in ("/dev/ttyUSB0", "/dev/ttyUSB1"):
        add_instrument_chain(
            cfg, [step("sim970", "2"), step("sim900", "5"), step("prologix_gpib", port)]
        )
    return cfg


def selection(cfg, port="/dev/ttyUSB1"):
    path = [
        {"type": t, "key": instrument_hash(t, k)}
        for t, k in [("prologix_gpib", port), ("sim900", "5"), ("sim970", "2")]
    ]
    nodes = get_configured_tree(cfg)
    for segment in path:
        node = next(n for n in nodes if n["key"] == segment["key"])
        nodes = list(node["children"].values())
    return path, node


def snapshot(cfg):
    return {str(p): p.read_bytes() for p in cfg.rglob("*.yml")}


def test_edit_uses_full_path_and_writes_only_selected_file(config):
    path, node = selection(config)
    before = snapshot(config)
    fields = deepcopy(node["fields"])
    fields["channels"]["0"]["settling_time"] = 0.42
    result = update_instrument_params(config, path, fields, node["fields"])
    assert selection(config)[1]["fields"]["channels"]["0"]["settling_time"] == 0.42
    assert (
        selection(config, "/dev/ttyUSB0")[1]["fields"]["channels"]["0"]["settling_time"]
        == 0.1
    )
    assert "settling_time: 0.42" in result["yaml"]
    assert "# (seconds)" in result["yaml"]
    after = snapshot(config)
    assert before.keys() == after.keys()
    assert sum(before[p] != after[p] for p in before) == 1


def test_stale_draft_cannot_overwrite_another_save(config):
    path, node = selection(config)
    fields = {**node["fields"], "offline": True}
    update_instrument_params(config, path, fields, node["fields"])
    with pytest.raises(ValueError, match="changed since"):
        update_instrument_params(config, path, node["fields"], node["fields"])
    assert selection(config)[1]["fields"]["offline"] is True


@pytest.mark.parametrize(
    "patch",
    [
        {"slot": "3"},
        {"type": "sim928"},
        {"enabled": False},
        {"children": {}},
        {"typo": 4},
        {"channels": {"0": {"max_retries": "invalid"}}},
        {"channels": {"99": {"max_retries": 2}}},
        {"channels": {"0": {"settling_typo": 0.2}}},
    ],
)
def test_invalid_or_structural_edit_does_not_write(config, patch):
    path, node = selection(config)
    before = snapshot(config)
    with pytest.raises((ValueError, ValidationError)):
        update_instrument_params(
            config, path, {**node["fields"], **patch}, node["fields"]
        )
    assert snapshot(config) == before


def test_parent_edit_preserves_disabled_child_references_and_bytes(config):
    path, _ = selection(config)
    parent_path = path[:1]
    root = next(
        n for n in get_configured_tree(config) if n["key"] == parent_path[0]["key"]
    )
    root_file = config / "instruments" / f"prologix_gpib_key_{root['key']}.yml"
    raw = _read_yaml(root_file)
    child_file = config / "instruments" / next(iter(raw["children"].values()))["ref"]
    child = _read_yaml(child_file)
    child["enabled"] = False
    _write_yaml(child_file, child)
    before = snapshot(config)
    update_instrument_params(
        config, parent_path, {**root["fields"], "baudrate": 19200}, root["fields"]
    )
    assert _read_yaml(root_file)["children"] == raw["children"]
    assert child_file.read_bytes() == before[str(child_file)]


def test_schema_and_yaml_come_from_params(config):
    schema = get_instrument_metadata()["sim970"]
    assert (
        schema["params_schema"]["$defs"]["Sim970ChannelParams"]["properties"][
            "settling_time"
        ]["type"]
        == "number"
    )
    assert "slot" in schema["read_only_fields"]
    _, node = selection(config)
    assert "channels:" in node["yaml"]
    assert "children:" not in node["yaml"]


@pytest.mark.parametrize("blocked", ["held", "claimed", "remote"])
def test_server_enforces_existing_edit_guards(config, blocked):
    path, node = selection(config)
    server = WireServer(
        bind=["ipc:///tmp/lw-editor-test.sock"],
        registry=InstrumentRegistry.from_config_dir(str(config)),
        config_dir=str(config),
    )
    root = f"inst://{path[0]['key']}"
    if blocked == "held":
        server._registry._index[root] = object()
    if blocked == "claimed":
        server._claims.acquire([root], holder="test run", peer="test", ttl_s=30)
    peer = Peer(transport="tcp" if blocked == "remote" else "ipc", identity="test")
    token = set_current_peer(peer)
    before = snapshot(config)
    try:
        with pytest.raises((ValueError, LocalOnlyError)):
            server.tree_update(
                path, {**node["fields"], "offline": True}, node["fields"]
            )
    finally:
        reset_current_peer(token)
    assert snapshot(config) == before


def test_server_reload_and_audit_after_edit(config):
    path, node = selection(config)
    server = WireServer(
        bind=["ipc:///tmp/lw-editor-test.sock"],
        registry=InstrumentRegistry.from_config_dir(str(config)),
        config_dir=str(config),
    )
    token = set_current_peer(Peer(transport="ipc", identity="test"))
    try:
        server.tree_update(
            path, {**node["fields"], "attribute_name": "sense_module"}, node["fields"]
        )
        assert "sense_module" in server._registry.list_attributes()
        assert server.events_recent()[0]["kind"] == "tree.update"
        assert "tree_update" in HARDWARE_RPC
    finally:
        reset_current_peer(token)


def test_http_update_delegates_to_server(config, monkeypatch):
    from lab_wizard.wizard.backend import hardware_access, main
    from lab_wizard.wizard.backend.models import Env

    path, node = selection(config)
    calls = []

    class Session:
        url = "ipc:///test"

        def call(self, rpc, payload):
            calls.append((rpc, payload))
            return {"fields": payload["fields"], "yaml": "offline: true\n"}

    @contextmanager
    def session(*args, **kwargs):
        yield Session()

    monkeypatch.setattr(hardware_access, "server_session", session)
    main.app.dependency_overrides[main.get_env] = lambda: Env(config_dir=config)
    try:
        client = TestClient(main.app, raise_server_exceptions=False)
        result = client.post(
            "/api/manage-instruments/update",
            json={
                "path": path,
                "fields": {**node["fields"], "offline": True},
                "expected_fields": node["fields"],
            },
        )
        assert result.status_code == 200, result.text
        assert calls[0][0] == "tree_update"
        assert calls[0][1]["path"] == path
    finally:
        main.app.dependency_overrides.clear()


def test_duplicate_attribute_names_are_rejected_before_persistence(config):
    path, node = selection(config)
    other = selection(config, "/dev/ttyUSB0")[1]
    fields = deepcopy(node["fields"])
    fields["channels"]["0"]["attribute_name"] = other["fields"]["channels"]["0"][
        "attribute_name"
    ]
    before = snapshot(config)
    with pytest.raises(ValueError, match="Duplicate attribute_name"):
        update_instrument_params(config, path, fields, node["fields"])
    assert snapshot(config) == before


def test_reset_and_remove_use_selected_path_with_repeated_slot_hashes(config):
    from lab_wizard.lib.utilities.config_io import (
        reinitialize_instrument,
        remove_instrument,
    )

    first_path, first = selection(config, "/dev/ttyUSB0")
    second_path, second = selection(config)
    for path, node in ((first_path, first), (second_path, second)):
        fields = deepcopy(node["fields"])
        fields["channels"]["0"]["settling_time"] = 0.75
        update_instrument_params(config, path, fields, node["fields"])
    reinitialize_instrument(config, "sim970", second_path[-1]["key"], path=second_path)
    assert selection(config)[1]["fields"]["channels"]["0"]["settling_time"] == 0.1
    assert (
        selection(config, "/dev/ttyUSB0")[1]["fields"]["channels"]["0"]["settling_time"]
        == 0.75
    )
    remove_instrument(config, "sim970", second_path[-1]["key"], path=second_path)
    assert selection(config, "/dev/ttyUSB0")[1]["type"] == "sim970"
    with pytest.raises(StopIteration):
        selection(config)


def test_add_rejects_a_controller_key_mislabeled_as_a_mainframe(config):
    path, _ = selection(config)
    before = snapshot(config)
    with pytest.raises(ValueError, match="not of type"):
        add_instrument_chain(config, [
            step("sim928", "3"),
            step("sim900", path[0]["key"], "use_existing"),
        ])
    assert snapshot(config) == before
