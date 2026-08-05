"""Phase A: a daemon that outlives its wizard, needs no address, and never
routes savers or plotters.

Three separate mistakes are covered here, all of which produced the same
user-visible symptom — "my instruments disappeared":

* the host's daemon dying with the GUI window that started it,
* a same-machine host being forced to pick and defend a tcp port,
* a project routing *any* instrument to a server and then failing on its saver.
"""

import pytest
import yaml

from lab_wizard.lib.client.composite_resources import LOCAL, CompositeResources
from lab_wizard.lib.server.server import _load_server_config
from lab_wizard.wizard.backend.server_control import (
    disable_hosting,
    enable_hosting,
    server_status,
)


# --------------------------- helpers ---------------------------


class _Source:
    """Stands in for a RemoteResources / ResourceConfig, as in test_server_registry."""

    def __init__(self, label):
        self.label = label
        self.closed = False

    def from_attribute(self, name):
        return f"{self.label}:{name}"

    def close(self):
        self.closed = True


class _LocalResources(_Source):
    """A local ResourceConfig also carries savers and plotters."""

    def __init__(self, label="local"):
        super().__init__(label)
        self.savers = {"main": "saver-params"}
        self.plotters = {"live": "plotter-params"}


class _Project:
    def __init__(self, resources):
        self.resources = resources


def _config_dir(tmp_path):
    config = tmp_path / "config"
    (config / "server").mkdir(parents=True)
    return config


# --------------------------- A2: savers never route ---------------------------


def test_savers_and_plotters_come_from_the_local_project():
    """Generated code calls ``Saver.from_config(resources, key=...)``.

    ``resources`` is whatever source the project was handed, so without a
    passthrough every routed project dies on ``exp.savers[key]`` — including one
    that routes a single instrument and keeps the rest local.
    """
    composite = CompositeResources(
        local=_LocalResources(),
        remotes={"cryo": _Source("cryo")},
        sources={"counter": "cryo"},
    )
    assert composite.savers["main"] == "saver-params"
    assert composite.plotters["live"] == "plotter-params"


def test_savers_stay_local_even_when_every_instrument_is_remote():
    """The ``--remote`` case. A saver writes *this* machine's database."""
    composite = CompositeResources.all_remote(
        _Project(_LocalResources()), "tcp://rack:1", connect=lambda url: _Source("rack")
    )
    assert composite.from_attribute("bias") == "rack:bias"
    assert composite.savers["main"] == "saver-params"


def test_a_project_without_savers_says_so_rather_than_raising_attribute_error():
    composite = CompositeResources(local=_Source("local"))
    with pytest.raises(ValueError, match="resolved locally"):
        _ = composite.savers


# --------------------------- A2: the remote default ---------------------------


def test_all_remote_routes_unmapped_attributes_to_the_server():
    """``--remote`` means *everything*, not "everything that was listed"."""
    composite = CompositeResources.all_remote(
        _Project(_LocalResources()), "tcp://rack:1", connect=lambda url: _Source("rack")
    )
    assert composite.source_of("anything") == "remote"
    assert composite.from_attribute("anything") == "rack:anything"


def test_the_local_default_is_unchanged_for_ordinary_projects():
    """Existing projects declare no sources and must keep resolving locally."""
    composite = CompositeResources(local=_LocalResources())
    assert composite.source_of("bias") == LOCAL
    assert composite.from_attribute("bias") == "local:bias"


def test_all_remote_dials_the_url_it_was_given():
    dialled = []
    CompositeResources.all_remote(
        _Project(_LocalResources()),
        "tcp://rack:1",
        connect=lambda url: dialled.append(url) or _Source("rack"),
    )
    assert dialled == ["tcp://rack:1"]


# --------------------------- A3: bind is optional ---------------------------


def test_a_host_serving_only_this_machine_needs_no_address(tmp_path):
    """The ipc endpoint is derived from config_dir, so nothing must be chosen."""
    config = _config_dir(tmp_path)
    path = config / "server" / "server.yaml"
    path.write_text(yaml.safe_dump({"server": {"ipc": True}}), encoding="utf-8")

    cfg = _load_server_config(path)
    assert cfg["server"].get("bind") is None


def test_an_empty_server_block_is_the_minimal_host_declaration(tmp_path):
    config = _config_dir(tmp_path)
    path = config / "server" / "server.yaml"
    path.write_text("server:\n", encoding="utf-8")

    assert _load_server_config(path)["server"] == {}


def test_a_config_with_no_way_to_be_reached_is_refused(tmp_path):
    """ipc off and no bind is a server nothing can connect to — say so at load."""
    config = _config_dir(tmp_path)
    path = config / "server" / "server.yaml"
    path.write_text(
        yaml.safe_dump({"server": {"ipc": False}}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no way to accept clients"):
        _load_server_config(path)


def test_a_tcp_bind_still_loads(tmp_path):
    config = _config_dir(tmp_path)
    path = config / "server" / "server.yaml"
    path.write_text(
        yaml.safe_dump({"server": {"bind": "tcp://0.0.0.0:12300"}}), encoding="utf-8"
    )

    assert _load_server_config(path)["server"]["bind"] == "tcp://0.0.0.0:12300"


# --------------------------- A3: hosting opt-in ---------------------------


def test_enable_hosting_creates_a_bindless_config_the_server_accepts(tmp_path):
    """The button and the daemon must agree on what a minimal host looks like."""
    config = _config_dir(tmp_path)
    status = enable_hosting(config)

    assert status["has_config"] is True
    assert status["bind"] is None
    # The file it wrote is one the server will actually start against.
    cfg = _load_server_config(config / "server" / "server.yaml")
    assert cfg["server"].get("bind") is None


def test_enable_hosting_does_not_clobber_an_existing_config(tmp_path):
    """It may already carry permission rules — the opt-in must be idempotent."""
    config = _config_dir(tmp_path)
    path = config / "server" / "server.yaml"
    original = {"server": {"bind": "tcp://0.0.0.0:12300"}, "permissions": {"rules": []}}
    path.write_text(yaml.safe_dump(original), encoding="utf-8")

    enable_hosting(config)
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == original


def test_a_workspace_without_a_config_is_a_client(tmp_path):
    """Absence of server.yaml is what keeps a satellite from racing the host."""
    config = _config_dir(tmp_path)
    assert server_status(config)["has_config"] is False


def test_disable_hosting_returns_the_workspace_to_a_client(tmp_path):
    config = _config_dir(tmp_path)
    enable_hosting(config)
    assert server_status(config)["has_config"] is True

    disable_hosting(config)
    assert server_status(config)["has_config"] is False
    assert not (config / "server" / "server.yaml").exists()
