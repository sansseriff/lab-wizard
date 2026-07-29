"""Transport-sharing declarations, held-vs-declared, and conflict detection."""

from typing import Any, Literal

import pytest
from pydantic import BaseModel, Field

from lab_wizard.lib.client.preflight import (
    Conflict,
    ConflictError,
    conflicts_for_roots,
    project_root_keys,
)
from lab_wizard.lib.instruments.dbay.dbay import DBayParams
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.server.registry import InstrumentRegistry, root_path


# --------------------------- declarations ---------------------------


def test_defaults_are_conservative():
    """An instrument that declares nothing must be treated as unshareable."""

    class Bare(BaseModel):
        type: Literal["bare"] = "bare"
        children: dict = Field(default_factory=dict)

        @property
        def inst(self):
            return object

        def create_inst(self):
            return object()

    from lab_wizard.lib.instruments.general.parent_child import CanInstantiate

    class Declared(Bare, CanInstantiate[object]):
        pass

    params = Declared()
    assert params.transport_sharing() == "exclusive"
    assert params.state_authority() == "inferred"
    assert params.transport_key() is None


def test_prologix_is_exclusive():
    """++addr is global controller state; two processes cannot interleave."""
    params = PrologixGPIBParams(port="/dev/ttyUSB0")
    assert params.transport_sharing() == "exclusive"
    assert params.state_authority() == "inferred"
    assert params.transport_key() == "serial:///dev/ttyUSB0"


@pytest.mark.parametrize(
    "kwargs,sharing,authority",
    [
        ({}, "shared", "subscribed"),
        ({"mode": "direct"}, "exclusive", "inferred"),
        (
            {"mode": "direct", "direct_transport": "serial", "serial_port": "/dev/x"},
            "exclusive",
            "inferred",
        ),
    ],
)
def test_dbay_sharing_depends_on_mode(kwargs, sharing, authority):
    """GUI mode sits behind a multiplexing server; direct modes do not.

    This is why sharing cannot be a per-class constant — it is a property of
    the *configured* transport.
    """
    params = DBayParams(**kwargs)
    assert params.transport_sharing() == sharing
    assert params.state_authority() == authority


# --------------------------- registry ---------------------------


class _Root(BaseModel):
    """Minimal root: the registry requires only create_inst()."""

    type: str = "fake"
    sharing: str = "exclusive"
    key: str | None = None
    children: dict = Field(default_factory=dict)

    @property
    def inst(self):
        return object

    def create_inst(self) -> Any:
        return object()

    def transport_sharing(self):
        return self.sharing

    def state_authority(self):
        return "inferred"

    def transport_key(self):
        return self.key


def test_root_path_collapses_children_and_channels():
    assert root_path("inst://abc") == "inst://abc"
    assert root_path("inst://abc/def") == "inst://abc"
    assert root_path("inst://abc/def/channel/3") == "inst://abc"


def test_declared_is_not_held():
    """A server serves everything configured but holds only what it opened.

    The distinction is the whole basis of arbitration: a running server that
    has touched nothing must not block a local project.
    """
    registry = InstrumentRegistry.from_instruments(
        {"aaa": _Root(), "bbb": _Root(sharing="shared")}
    )
    assert set(registry.list_paths()) == {"inst://aaa", "inst://bbb"}
    assert registry.list_held() == []
    assert registry.held_roots() == set()

    registry.resolve("inst://aaa")
    assert registry.list_held() == ["inst://aaa"]
    assert registry.held_roots() == {"inst://aaa"}


def test_exclusive_roots_excludes_shared():
    registry = InstrumentRegistry.from_instruments(
        {"aaa": _Root(), "bbb": _Root(sharing="shared")}
    )
    assert set(registry.exclusive_roots()) == {"inst://aaa"}


def test_transport_declarations_reach_describe_path():
    registry = InstrumentRegistry.from_instruments(
        {"aaa": _Root(sharing="shared", key="tcp://host:1")}
    )
    desc = registry.describe_path("inst://aaa")
    assert desc["transport_sharing"] == "shared"
    assert desc["transport_key"] == "tcp://host:1"


def test_root_without_declarations_still_registers():
    """Duck-typed roots exist (tests, adapters); they must not break hosting."""

    class Undeclared(BaseModel):
        type: str = "undeclared"
        children: dict = Field(default_factory=dict)

        @property
        def inst(self):
            return object

        def create_inst(self):
            return object()

    registry = InstrumentRegistry.from_instruments({"zzz": Undeclared()})
    assert registry.list_paths() == ["inst://zzz"]
    assert registry.transport_for("inst://zzz")["transport_sharing"] == "exclusive"


# --------------------------- conflict detection ---------------------------


def _held(exclusive=(), held_roots=(), held_paths=()):
    return {
        "exclusive_roots": {r: {"transport_key": f"serial://{r}"} for r in exclusive},
        "held_roots": list(held_roots),
        "held_paths": list(held_paths),
    }


def test_no_conflict_when_server_holds_nothing():
    """A server being up is not, by itself, a conflict."""
    assert conflicts_for_roots(
        ["inst://aaa"], _held(exclusive=["inst://aaa"])
    ) == []


def test_conflict_when_server_holds_an_exclusive_root():
    conflicts = conflicts_for_roots(
        ["inst://aaa"],
        _held(
            exclusive=["inst://aaa"],
            held_roots=["inst://aaa"],
            held_paths=["inst://aaa", "inst://aaa/child"],
        ),
    )
    assert [c.root for c in conflicts] == ["inst://aaa"]
    assert conflicts[0].held_paths == ["inst://aaa", "inst://aaa/child"]


def test_shared_root_never_conflicts_even_when_held():
    """A DBay rack in GUI mode is multiplexed; both processes may use it."""
    assert (
        conflicts_for_roots(
            ["inst://bbb"], _held(exclusive=[], held_roots=["inst://bbb"])
        )
        == []
    )


def test_unrelated_held_root_is_ignored():
    assert (
        conflicts_for_roots(
            ["inst://aaa"],
            _held(exclusive=["inst://zzz"], held_roots=["inst://zzz"]),
        )
        == []
    )


def test_project_root_keys_from_config_keys():
    assert project_root_keys({"aaa": object(), "bbb": object()}) == {
        "inst://aaa",
        "inst://bbb",
    }


def test_conflict_error_names_the_rack_and_the_fix():
    """The error has to be actionable — that is the entire point of preflight."""
    err = ConflictError(
        [Conflict("inst://aaa", "serial:///dev/ttyUSB0", ["inst://aaa"])],
        "tcp://127.0.0.1:12300",
    )
    text = str(err)
    assert "serial:///dev/ttyUSB0" in text
    assert "tcp://127.0.0.1:12300" in text
    assert "stop the server" in text.lower()


def test_preflight_skips_when_no_server_configured():
    from lab_wizard.lib.client.preflight import preflight_local_project

    preflight_local_project({"aaa": object()}, None)  # must not raise


def test_preflight_tolerates_unreachable_server():
    """A configured-but-stopped server must not stop a local project running."""
    from lab_wizard.lib.client.preflight import preflight_local_project

    preflight_local_project(
        {"aaa": object()}, "tcp://127.0.0.1:1", timeout_ms=200
    )  # must not raise
