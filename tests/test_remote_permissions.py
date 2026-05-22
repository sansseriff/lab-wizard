"""Integration tests: permission gate enforced over the live client/server wire."""

from __future__ import annotations

import socket
import threading
import time
from typing import Iterator

import pytest

from lab_wizard.lib.client.remote_exp import RemoteExp
from lab_wizard.lib.client.session import PermissionDeniedError, RemoteCallError
from lab_wizard.lib.instruments.general.state_effects import Arg
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.server.permissions import PermissionGate, load_permissions
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _FakeFuncGen:
    """A unique instrument with no shared ABC — exercises reflective forwarding."""

    _state_methods_ = {"arm": ("armed", True), "disarm": ("armed", False)}

    def __init__(self) -> None:
        self.pulses = 0

    def pulse(self) -> str:
        self.pulses += 1
        return "pulsed"

    def configure(self) -> str:
        return "configured"


class _PermFixture:
    def __init__(self) -> None:
        bias = StandInVSource()
        funcgen = _FakeFuncGen()
        self.bias = bias
        self.funcgen = funcgen

        reg = InstrumentRegistry.__new__(InstrumentRegistry)
        reg._index = {
            f"{PATH_PREFIX}dac/channel/0": bias,
            f"{PATH_PREFIX}fg/funcgen/0": funcgen,
        }
        reg._attribute_index = {
            "bias": f"{PATH_PREFIX}dac/channel/0",
            "fg": f"{PATH_PREFIX}fg/funcgen/0",
        }
        self.registry = reg

        gate = PermissionGate(load_permissions({
            "state_defaults": {f"{PATH_PREFIX}dac/channel/0": {"voltage": 0.0}},
            "rules": [{
                "id": "cryo_amp_safety",
                "description": "no pulsing while biased",
                "when": {
                    "all": [{
                        "path": f"{PATH_PREFIX}dac/channel/0",
                        "key": "voltage",
                        "greater_than": 0.0,
                    }]
                },
                "deny": [{"path_glob": f"{PATH_PREFIX}*/funcgen/*", "methods": ["pulse"]}],
                "message": "Bias energized; set channel 0 to 0 V before pulsing.",
            }],
        }))

        self.bind = f"tcp://127.0.0.1:{_free_tcp_port()}"
        self.server = WireServer(bind=self.bind, registry=self.registry, gate=gate)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.3)
        self.exp = RemoteExp.connect(self.bind, timeout_ms=3000)

    def close(self) -> None:
        self.exp.close()
        self.server.stop()
        self._thread.join(timeout=2)


@pytest.fixture
def perm() -> Iterator[_PermFixture]:
    fix = _PermFixture()
    try:
        yield fix
    finally:
        fix.close()


def test_pulse_allowed_when_bias_off(perm: _PermFixture) -> None:
    fg = perm.exp.from_attribute("fg")
    assert fg.pulse() == "pulsed"
    assert perm.funcgen.pulses == 1


def test_pulse_denied_when_bias_on(perm: _PermFixture) -> None:
    bias = perm.exp.from_attribute("bias", StandInVSource)
    fg = perm.exp.from_attribute("fg")

    # Energize the bias channel — the server records voltage state.
    bias.set_voltage(0.8)

    with pytest.raises(PermissionDeniedError) as exc_info:
        fg.pulse()

    err = exc_info.value
    assert err.rule_id == "cryo_amp_safety"
    assert "Bias energized" in err.message
    assert err.blocking_state == {f"{PATH_PREFIX}dac/channel/0#voltage": 0.8}
    # The blocked call never reached the instrument.
    assert perm.funcgen.pulses == 0


def test_permission_denied_is_remote_call_error(perm: _PermFixture) -> None:
    bias = perm.exp.from_attribute("bias", StandInVSource)
    fg = perm.exp.from_attribute("fg")
    bias.set_voltage(0.8)
    # PermissionDeniedError subclasses RemoteCallError, so generic handlers work.
    with pytest.raises(RemoteCallError):
        fg.pulse()


def test_unrelated_method_not_blocked(perm: _PermFixture) -> None:
    bias = perm.exp.from_attribute("bias", StandInVSource)
    fg = perm.exp.from_attribute("fg")
    bias.set_voltage(0.8)
    # `configure` is not in the deny clause's methods, so it passes.
    assert fg.configure() == "configured"


def test_reopens_after_bias_cleared(perm: _PermFixture) -> None:
    bias = perm.exp.from_attribute("bias", StandInVSource)
    fg = perm.exp.from_attribute("fg")

    bias.set_voltage(0.8)
    with pytest.raises(PermissionDeniedError):
        fg.pulse()

    bias.set_voltage(0.0)  # stand down
    assert fg.pulse() == "pulsed"
    assert perm.funcgen.pulses == 1
