"""Configuration preservation and complete, cancelable instrument additions."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from lab_wizard.lib.utilities import config_io as io
from lab_wizard.lib.server.peer import (
    Peer,
    LocalOnlyError,
    set_current_peer,
    reset_current_peer,
)
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer, HARDWARE_RPC
from lab_wizard.wizard.backend.instrument_drafts import InstrumentDrafts


def step(type, key, action="create_new", **extra):
    return {"type": type, "key": key, "action": action, **extra}


def chain(port="/dev/tty.one"):
    return [step("sim928", "1"), step("sim900", "5"), step("prologix_gpib", port)]


def path(port="/dev/tty.one"):
    return [
        {"type": t, "key": io.instrument_hash(t, k)}
        for t, k in [("prologix_gpib", port), ("sim900", "5")]
    ]


def bench_controller(rig):
    """A draft of the simulated bench's controller, for discovery that opens it.

    The short timeout keeps a scan of all 30 GPIB addresses cheap.
    """
    return [step("prologix_gpib", rig.port, extra={"timeout": 0.05})]


def existing(port="/dev/tty.one"):
    return [step(p["type"], p["key"], "use_existing") for p in reversed(path(port))]


def snapshot(config):
    return {p.relative_to(config): p.read_bytes() for p in config.rglob("*.yml")}


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_SERVER_REGISTRY", str(tmp_path / "servers"))
    monkeypatch.setenv("LAB_WIZARD_LEASE_DIR", str(tmp_path / "leases"))
    config = tmp_path / "config"
    for port in ["/dev/tty.one", "/dev/tty.two"]:
        io.add_instrument_chain(config, chain(port))
    return config


def server(config):
    return WireServer(
        bind="ipc:///tmp/draft-tests.sock",
        registry=InstrumentRegistry.from_config_dir(config),
        config_dir=str(config),
    )


@contextmanager
def local_peer():
    token = set_current_peer(Peer(transport="ipc", identity="test"))
    try:
        yield
    finally:
        reset_current_peer(token)


@pytest.mark.parametrize("operation", ["remove", "reset", "add", "normalize"])
def test_edits_preserve_disabled_roots_and_children(config, operation):
    instruments = io.load_instruments(config)
    disabled_root = instruments[path("/dev/tty.two")[0]["key"]]
    disabled_root.enabled = False
    disabled_child = io.find_instrument_at_path(instruments, path()).children[
        io.instrument_hash("sim928", "1")
    ]
    disabled_child.enabled = False
    io.save_instruments_to_config(instruments, config)
    old_root, old_child = disabled_root.model_dump(), disabled_child.model_dump()
    if operation == "remove":
        io.add_instrument_chain(config, [step("prologix_gpib", "/dev/tty.delete-me")])
        io.remove_instrument(
            config, "prologix_gpib", io.instrument_hash("prologix_gpib", "/dev/tty.delete-me")
        )
    elif operation == "reset":
        io.reinitialize_instrument(config, "prologix_gpib", path()[0]["key"])
    elif operation == "add":
        io.add_instrument_chain(config, [step("sim928", "2"), *existing()])
    else:
        io.normalize_instruments(config)
    saved = io.load_instruments(config, include_disabled=True)
    assert saved[path("/dev/tty.two")[0]["key"]].model_dump() == old_root
    assert (
        io.find_instrument_at_path(saved, path())
        .children[io.instrument_hash("sim928", "1")]
        .model_dump()
        == old_child
    )
    active = io.load_instruments(config)
    assert path("/dev/tty.two")[0]["key"] not in active
    assert (
        io.instrument_hash("sim928", "1")
        not in io.find_instrument_at_path(active, path()).children
    )


def test_remove_leaves_unrelated_unreferenced_yaml(config):
    orphan = config / "instruments" / "notes" / "saved.yml"
    orphan.parent.mkdir()
    orphan.write_text("note: keep this\n")
    io.remove_instrument(config, "prologix_gpib", path()[0]["key"])
    assert orphan.read_text() == "note: keep this\n"


@pytest.mark.parametrize("duplicate", [chain(), [step("sim928", "1"), *existing()]])
def test_duplicate_add_does_not_change_a_single_file(config, duplicate):
    before = snapshot(config)
    with pytest.raises(ValueError, match="already exists"):
        io.add_instrument_chain(config, duplicate)
    assert snapshot(config) == before


def test_disabled_address_is_also_reserved(config):
    instruments = io.load_instruments(config)
    instruments[path()[0]["key"]].enabled = False
    io.save_instruments_to_config(instruments, config)
    before = snapshot(config)
    with pytest.raises(ValueError, match="already exists"):
        io.add_instrument_chain(config, chain())
    assert snapshot(config) == before


def test_failed_multifile_save_rolls_back(config, monkeypatch):
    before = snapshot(config)
    write = io._write_yaml
    count = 0

    def fail(path, data):
        nonlocal count
        count += 1
        if count == 3:
            raise OSError("disk full")
        return write(path, data)

    monkeypatch.setattr(io, "_write_yaml", fail)
    with pytest.raises(OSError, match="disk full"):
        io.add_instrument_chain(config, chain("/dev/tty.new"))
    assert snapshot(config) == before


def test_discovery_targets_exact_nested_parent_and_preserves_existing(config):
    spec = [{"type": "sim928", "key_fields": {"slot": "2"}}]
    target = path("/dev/tty.two")
    io.apply_discovered_children(
        config, target[-1]["type"], target[-1]["key"], spec, target
    )
    saved = io.load_instruments(config)
    new_key = io.instrument_hash("sim928", "2")
    assert new_key in io.find_instrument_at_path(saved, target).children
    assert new_key not in io.find_instrument_at_path(saved, path()).children
    before = snapshot(config)
    result = io.apply_discovered_children(
        config, target[-1]["type"], target[-1]["key"], spec, target
    )
    assert result["added"] == []
    assert snapshot(config) == before


def test_discovery_targets_exact_root_and_rejects_wrong_key(config):
    target = path("/dev/tty.two")[0]
    spec = [{"type": "sim900", "key_fields": {"gpib_address": "8"}}]
    io.apply_discovered_children(config, target["type"], target["key"], spec)
    saved = io.load_instruments(config)
    key = io.instrument_hash("sim900", "8")
    assert key in saved[target["key"]].children
    assert key not in saved[path()[0]["key"]].children
    before = snapshot(config)
    with pytest.raises(ValueError):
        io.apply_discovered_children(config, target["type"], "not-a-key", spec)
    assert snapshot(config) == before


@pytest.mark.parametrize("blocked", ["held", "claimed", "remote"])
def test_discovery_write_has_server_guards(config, blocked):
    owner = server(config)
    target = path()
    root = "inst://" + target[0]["key"]
    if blocked == "held":
        owner._registry._index[root] = object()
    elif blocked == "claimed":
        owner._claims.acquire([root], holder="run", peer="test", ttl_s=30)
    token = set_current_peer(
        Peer(transport="tcp" if blocked == "remote" else "ipc", identity="test")
    )
    before = snapshot(config)
    try:
        with pytest.raises((ValueError, LocalOnlyError)):
            owner.tree_apply_children("sim900", target[-1]["key"], [], target)
    finally:
        reset_current_peer(token)
    assert snapshot(config) == before


def test_discovery_write_reloads_and_audits(config):
    owner = server(config)
    target = path()
    with local_peer():
        owner.tree_apply_children(
            "sim900",
            target[-1]["key"],
            [{"type": "sim928", "key_fields": {"slot": "3"}}],
            target,
        )
    assert any(
        p.endswith(io.instrument_hash("sim928", "3"))
        for p in owner._registry.list_paths()
    )
    assert owner.events_recent()[0]["kind"] == "tree.apply_children"
    assert "tree_apply_children" in HARDWARE_RPC


def test_cancel_draft_never_writes_config(config):
    store = InstrumentDrafts()
    before = snapshot(config)
    key = store.create(config)
    preview = store.stage(config, key, chain("/dev/tty.new")[1:])
    assert preview["path"] == path("/dev/tty.new")
    assert snapshot(config) == before
    store.cancel(config, key)
    assert snapshot(config) == before
    with pytest.raises(ValueError, match="expired"):
        store.commit(config, key, lambda c: io.add_instrument_chain(config, c))


def test_commit_entire_chain_once_even_when_retried(config):
    store = InstrumentDrafts()
    key = store.create(config)
    store.stage(config, key, chain("/dev/tty.new"))

    def commit():
        return store.commit(config, key, lambda c: io.add_instrument_chain(config, c))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: commit(), range(2)))
    assert results[0] == results[1]
    assert len(io.load_instruments(config)) == 3


def test_commit_rechecks_conflicts_since_staging(config):
    store = InstrumentDrafts()
    key = store.create(config)
    store.stage(config, key, chain("/dev/tty.new"))
    io.add_instrument_chain(config, chain("/dev/tty.new"))
    before = snapshot(config)
    with pytest.raises(ValueError, match="already exists"):
        store.commit(config, key, lambda c: io.add_instrument_chain(config, c))
    assert snapshot(config) == before


def test_drafts_are_workspace_scoped_and_expire(config):
    store = InstrumentDrafts()
    key = store.create(config)
    with pytest.raises(ValueError, match="unavailable"):
        store.stage(config.parent / "another", key, chain("/dev/tty.new"))
    store.get(config, key).touched -= store.ttl + 1
    with pytest.raises(ValueError, match="expired"):
        store.stage(config, key, chain("/dev/tty.new"))


def test_invalid_discovered_child_does_not_save_parent(config):
    new_chain = [
        step(
            "prologix_gpib",
            "/dev/tty.new",
            children=[{"type": "sim900", "key_fields": {"typo": "3"}}],
        )
    ]
    before = snapshot(config)
    with pytest.raises(ValueError, match="Invalid discovery key"):
        io.add_instrument_chain(config, new_chain)
    assert snapshot(config) == before


def test_discover_unsaved_parent_releases_root_and_writes_nothing(config, monkeypatch, rig):
    from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIB

    owner = server(config)
    closed = []
    disconnect = PrologixGPIB.disconnect

    def close(self):
        closed.append(self)
        return disconnect(self)

    monkeypatch.setattr(PrologixGPIB, "disconnect", close)
    before = snapshot(config)
    with local_peer():
        result = owner.discover(
            "sim900", "scan_gpib", draft_chain=bench_controller(rig)
        )
    assert result["result_type"] == "self_candidates"
    assert len(closed) == 1
    assert snapshot(config) == before
    assert owner._registry.list_held() == []


@pytest.fixture
def client(config, monkeypatch):
    from lab_wizard.wizard.backend import hardware_access, main
    from lab_wizard.wizard.backend.models import Env

    @contextmanager
    def no_server(*a, **kw):
        yield None

    monkeypatch.setattr(hardware_access, "server_session", no_server)
    main.app.dependency_overrides[main.get_env] = lambda: Env(config_dir=config)
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(main.get_env)


def test_http_draft_discover_and_cancel(client, config, rig):
    before = snapshot(config)
    draft = client.post("/api/manage-instruments/drafts").json()["id"]
    url = f"/api/manage-instruments/drafts/{draft}"
    response = client.put(url, json={"chain": bench_controller(rig)})
    assert response.status_code == 200, response.text
    response = client.post(
        "/api/manage-instruments/discover",
        json={"type": "sim900", "action": "scan_gpib", "draft_id": draft},
    )
    assert response.status_code == 200, response.text
    assert client.delete(url).status_code == 200
    assert snapshot(config) == before


def test_http_commit_with_discovered_children_is_one_save(client, config):
    draft = client.post("/api/manage-instruments/drafts").json()["id"]
    url = f"/api/manage-instruments/drafts/{draft}"
    staged = [
        step(
            "prologix_gpib",
            "/dev/tty.new",
            children=[{"type": "sim900", "key_fields": {"gpib_address": "8"}}],
        )
    ]
    response = client.put(url, json={"chain": staged})
    assert response.status_code == 200, response.text
    first = client.post(url + "/commit")
    assert first.status_code == 200, first.text
    second = client.post(url + "/commit")
    assert first.json() == second.json()
    saved = io.load_instruments(config)
    assert (
        io.instrument_hash("sim900", "8")
        in saved[io.instrument_hash("prologix_gpib", "/dev/tty.new")].children
    )


def test_http_discovery_write_uses_live_server(client, config, monkeypatch):
    from lab_wizard.wizard.backend import hardware_access

    calls = []

    class Session:
        url = "ipc:///test"

        def call(self, rpc, payload):
            calls.append((rpc, payload))
            return {"status": "ok"}

    @contextmanager
    def live(*a, **kw):
        yield Session()

    monkeypatch.setattr(hardware_access, "server_session", live)
    target = path()
    payload = {
        "parent_type": "sim900",
        "parent_key": target[-1]["key"],
        "path": target,
        "children": [],
    }
    assert (
        client.post("/api/manage-instruments/apply-children", json=payload).status_code
        == 200
    )
    assert calls == [("tree_apply_children", payload)]


def test_wrong_child_family_is_rejected_before_any_write(config):
    before = snapshot(config)
    with pytest.raises(ValueError):
        io.add_instrument_chain(config, [step("yoko_attenuator", "8"), *existing()])
    assert snapshot(config) == before


def test_discovery_failure_still_releases_root(config, monkeypatch, rig):
    from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
    from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIB
    from lab_wizard.lib.instruments.general.discovery import DiscoveryAction, NoParams

    closed = []
    original = PrologixGPIB.disconnect

    def close(self):
        closed.append(self)
        return original(self)

    def fail(params, parent_inst):
        raise RuntimeError("scan failed")

    monkeypatch.setattr(PrologixGPIB, "disconnect", close)
    monkeypatch.setattr(
        Sim900Params,
        "discovery_actions",
        classmethod(
            lambda cls: [
                DiscoveryAction(
                    name="fail",
                    label="Fail",
                    description="",
                    params_model=NoParams,
                    handler=fail,
                    parent_dep="prologix_gpib",
                )
            ]
        ),
    )
    before = snapshot(config)
    with local_peer(), pytest.raises(RuntimeError, match="scan failed"):
        server(config).discover("sim900", "fail", draft_chain=bench_controller(rig))
    assert len(closed) == 1
    assert snapshot(config) == before


def test_add_under_free_rack_ignores_matching_child_hash_in_held_rack(config):
    owner = server(config)
    held_root = "inst://" + path("/dev/tty.two")[0]["key"]
    owner._registry._index[held_root] = object()
    with local_peer():
        result = owner.tree_add([step("sim928", "3"), *existing()])
    assert result["status"] == "ok"
    assert held_root in owner._registry.list_held()
    saved = io.load_instruments(config)
    key = io.instrument_hash("sim928", "3")
    assert key in io.find_instrument_at_path(saved, path()).children
    assert key not in io.find_instrument_at_path(saved, path("/dev/tty.two")).children


def test_draft_discovery_keeps_disabled_hardware_out_of_runtime(config):
    from lab_wizard.lib.utilities.instrument_discovery import draft_discovery_tree

    instruments = io.load_instruments(config)
    module_key = io.instrument_hash("sim928", "1")
    io.find_instrument_at_path(instruments, path()).children[module_key].enabled = False
    io.save_instruments_to_config(instruments, config)
    before = snapshot(config)
    registry, _ = draft_discovery_tree(config, [step("sim928", "3"), *existing()])
    assert all(not p.endswith(module_key) for p in registry.list_paths())
    assert any(
        p.endswith(io.instrument_hash("sim928", "3")) for p in registry.list_paths()
    )
    assert snapshot(config) == before
