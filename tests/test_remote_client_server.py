"""End-to-end tests for the remote instrument client/server (Phase 1 + 2).

These spin up a real ``WireServer`` (ZMQ ROUTER + pyleco message framing) in a
background thread, backed by stand-in instruments so no hardware is required,
and drive it through ``RemoteResources`` + typed proxies — including the typed
``from_attribute(name, as_type=...)`` overload.

The instruments used here (``StandInVSource`` / ``StandInVSense``) are ordinary
classes that implement the ``VSource`` / ``VSense`` ABCs, so they double as the
``as_type`` argument: the client gets concrete-class typing while the runtime
object stays a forwarding proxy.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Iterator

import pytest

from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource
from lab_wizard.lib.client.remote_resources import RemoteResources
from lab_wizard.lib.client.session import RemoteCallError
from lab_wizard.lib.instruments.general.vsense import StandInVSense, VSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource, VSource
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer


def _free_tcp_port() -> int:
    """Grab an ephemeral localhost port (small race window, fine for tests)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_registry() -> tuple[InstrumentRegistry, StandInVSource, StandInVSense]:
    """A registry holding one stand-in VSource and one VSense, bypassing YAML."""
    reg = InstrumentRegistry.__new__(InstrumentRegistry)
    reg._index = {}
    reg._attribute_index = {}
    vsource = StandInVSource()
    vsense = StandInVSense()
    reg._index[f"{PATH_PREFIX}vsource"] = vsource
    reg._index[f"{PATH_PREFIX}vsense"] = vsense
    reg._attribute_index["bias"] = f"{PATH_PREFIX}vsource"
    reg._attribute_index["sense"] = f"{PATH_PREFIX}vsense"
    return reg, vsource, vsense


class _RemoteFixture:
    """Bundle of the live server, its backing instruments, and client resources."""

    def __init__(self) -> None:
        self.registry, self.vsource, self.vsense = _build_registry()
        self.bind = f"tcp://127.0.0.1:{_free_tcp_port()}"
        self.server = WireServer(bind=self.bind, registry=self.registry)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.3)  # let the ROUTER socket bind
        self.resources = RemoteResources.connect(self.bind, timeout_ms=3000)

    def close(self) -> None:
        self.resources.close()
        self.server.stop()
        self._thread.join(timeout=2)


@pytest.fixture
def remote() -> Iterator[_RemoteFixture]:
    fix = _RemoteFixture()
    try:
        yield fix
    finally:
        fix.close()


def test_typed_vsource_round_trip(remote: _RemoteFixture) -> None:
    # Typed handle: pyright treats `bias` as StandInVSource (ctrl-click,
    # autocomplete, signature checks all point at the concrete class).
    bias = remote.resources.from_attribute("bias", StandInVSource)

    # It satisfies the VSource ABC at runtime even though it's a proxy.
    assert isinstance(bias, VSource)
    assert isinstance(bias, RemoteVSource)

    # Method calls forward over the wire and mutate the server-side object.
    assert bias.set_voltage(0.75) is True
    assert remote.vsource.voltage == 0.75

    assert bias.turn_on() is True
    assert remote.vsource.output_enabled is True

    assert bias.turn_off() is True
    assert remote.vsource.output_enabled is False


def test_typed_vsense_round_trip(remote: _RemoteFixture) -> None:
    sense = remote.resources.from_attribute("sense", StandInVSense)
    assert isinstance(sense, VSense)
    assert isinstance(sense, RemoteVSense)

    # Set the value on the server, read it back through the proxy.
    remote.vsense.measurement_value = 1.234
    assert sense.get_voltage() == 1.234

    # measure() is a concrete VSense wrapper that calls get_voltage();
    # on the proxy get_voltage is a forwarder, so measure() works too.
    assert sense.measure() == 1.234


def test_untyped_from_attribute_still_works(remote: _RemoteFixture) -> None:
    # Without as_type, the proxy is selected from the server-reported ABC.
    bias = remote.resources.from_attribute("bias")
    assert isinstance(bias, RemoteVSource)
    assert bias.set_voltage(0.1) is True
    assert remote.vsource.voltage == 0.1


def test_proxy_is_cached(remote: _RemoteFixture) -> None:
    a = remote.resources.from_attribute("bias", StandInVSource)
    b = remote.resources.from_attribute("bias", StandInVSource)
    assert a is b


def test_as_type_is_static_only_lie(remote: _RemoteFixture) -> None:
    # The runtime object is the proxy, never the concrete server class.
    bias = remote.resources.from_attribute("bias", StandInVSource)
    assert isinstance(bias, RemoteVSource)
    assert not isinstance(bias, StandInVSource)


def test_discovery_apis(remote: _RemoteFixture) -> None:
    assert remote.resources.list_attributes() == ["bias", "sense"]

    info = remote.resources.describe_attribute("bias")
    assert info["behavior_abc"] == "VSource"
    assert info["type_hint"] == "StandInVSource"
    assert info["path"] == f"{PATH_PREFIX}vsource"

    descriptions = remote.resources.list_descriptions()
    by_name = {d["attribute_name"]: d for d in descriptions}
    assert by_name["bias"]["behavior_abc"] == "VSource"
    assert by_name["sense"]["behavior_abc"] == "VSense"


def test_unknown_attribute_raises(remote: _RemoteFixture) -> None:
    with pytest.raises(RemoteCallError):
        remote.resources.from_attribute("does_not_exist")


def test_unknown_method_raises(remote: _RemoteFixture) -> None:
    bias = remote.resources.from_attribute("bias", StandInVSource)
    # Reflective fallback forwards the call; the server has no such method,
    # so it comes back as a structured RemoteCallError.
    with pytest.raises(RemoteCallError):
        # type: ignore[attr-defined]  # intentionally not on StandInVSource
        bias.totally_made_up_method()  # pyright: ignore[reportAttributeAccessIssue]
