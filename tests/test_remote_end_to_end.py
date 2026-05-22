"""End-to-end narrative: driving instruments through the server with the
permission state machine enforcing a cryo-amp safety interlock.

This reads as one continuous story so you can see exactly how state is recorded
on the server and how it gates calls as conditions change. Run it two ways:

    pytest tests/test_remote_end_to_end.py -v        # as an assertion-checked test
    python -m tests.test_remote_end_to_end           # watch it narrate each step

The scenario:
    * A bias voltage source ("bias_source") feeds a sensitive cryo amplifier.
    * A function generator ("pulse_gen") can emit pulses.
    * Safety rule: while the bias source is energized (> 0 V), pulsing the
      function generator could damage the amplifier, so it must be blocked.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Callable

from lab_wizard.lib.client.remote_exp import RemoteExp
from lab_wizard.lib.client.session import PermissionDeniedError
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.server.permissions import PermissionGate, load_permissions
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer

# --------------------------- a small function generator ---------------------------


class PulseGenerator:
    """A one-of-a-kind instrument with no shared ABC. Driven over the wire
    purely by the proxy's reflective forwarding — no proxy class needed."""

    # Declares how its methods influence safety state (for completeness;
    # this scenario keys the rule off the bias source instead).
    _state_methods_ = {"arm": ("armed", True), "disarm": ("armed", False)}

    def __init__(self) -> None:
        self.pulse_count = 0

    def pulse(self) -> str:
        self.pulse_count += 1
        return f"pulse #{self.pulse_count} emitted"


# --------------------------- server setup ---------------------------

BIAS_PATH = f"{PATH_PREFIX}cryostat/dac/channel/0"
PULSE_PATH = f"{PATH_PREFIX}bench/funcgen/0"


def _free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _build_gate() -> PermissionGate:
    return PermissionGate(
        load_permissions(
            {
                # Assume the bias is at 0 V until told otherwise.
                "state_defaults": {BIAS_PATH: {"voltage": 0.0}},
                "rules": [
                    {
                        "id": "cryo_amp_safety",
                        "description": "No pulsing the function generator while the cryo "
                        "bias source is energized.",
                        "when": {
                            "all": [
                                {
                                    "path": BIAS_PATH,
                                    "key": "voltage",
                                    "greater_than": 0.0,
                                }
                            ]
                        },
                        "deny": [
                            {
                                "path_glob": f"{PATH_PREFIX}*/funcgen/*",
                                "methods": ["pulse"],
                            }
                        ],
                        "message": "Cryo bias is energized — set the bias source to 0 V "
                        "before pulsing.",
                    }
                ],
            }
        )
    )


class Lab:
    """A running server + its backing instruments + a connected client."""

    def __init__(self) -> None:
        self.bias = StandInVSource()
        self.pulse_gen = PulseGenerator()

        registry = InstrumentRegistry.__new__(InstrumentRegistry)
        registry._index = {BIAS_PATH: self.bias, PULSE_PATH: self.pulse_gen}
        registry._attribute_index = {
            "bias_source": BIAS_PATH,
            "pulse_gen": PULSE_PATH,
        }

        self.gate = _build_gate()
        self.bind = f"tcp://127.0.0.1:{_free_tcp_port()}"
        self.server = WireServer(bind=self.bind, registry=registry, gate=self.gate)
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.3)
        self.exp = RemoteExp.connect(self.bind, timeout_ms=3000)

    def server_state(self) -> dict:
        """Peek at what the server's state tracker currently believes."""
        return self.gate.tracker.snapshot()

    def close(self) -> None:
        self.exp.close()
        self.server.stop()
        self._thread.join(timeout=2)


# --------------------------- the scenario ---------------------------


def run_scenario(say: Callable[[str], None] = lambda _msg: None) -> None:
    """Drive the full story. `say` narrates; assertions verify each step."""
    lab = Lab()
    try:
        say("\n== Client connects and discovers what's available ==")
        attrs = lab.exp.list_attributes()
        say(f"  available attributes: {attrs}")
        assert attrs == ["bias_source", "pulse_gen"]

        # Typed handle for the bias source; reflective handle for the funcgen.
        bias = lab.exp.from_attribute("bias_source", StandInVSource)
        pulse_gen = lab.exp.from_attribute("pulse_gen", PulseGenerator)

        say("\n== Step 1: bias is off (0 V). Pulsing is safe. ==")
        say(f"  server state: {lab.server_state()}")
        result = pulse_gen.pulse()
        say(f"  pulse_gen.pulse() -> {result!r}")
        assert result == "pulse #1 emitted"
        assert lab.pulse_gen.pulse_count == 1

        say("\n== Step 2: energize the bias source to 0.8 V ==")
        bias.set_voltage(0.8)
        # The server recorded the new state when the set_voltage RPC ran.
        say(f"  server state after set_voltage(0.8): {lab.server_state()}")
        assert lab.server_state()[f"{BIAS_PATH}#voltage"] == 0.8
        assert lab.bias.voltage == 0.8  # underlying instrument really moved

        say("\n== Step 3: try to pulse while biased — the gate blocks it ==")
        try:
            pulse_gen.pulse()
            raise AssertionError("expected the pulse to be blocked")
        except PermissionDeniedError as denied:
            say(f"  BLOCKED: {denied.message}")
            say(f"  rule_id: {denied.rule_id}")
            say(f"  blocking_state: {denied.blocking_state}")
            assert denied.rule_id == "cryo_amp_safety"
            assert denied.blocking_state == {f"{BIAS_PATH}#voltage": 0.8}
        # The blocked call never reached the hardware.
        assert lab.pulse_gen.pulse_count == 1
        say("  (pulse count unchanged — the call never reached the instrument)")

        say("\n== Step 4: stand down the bias to 0 V ==")
        bias.set_voltage(0.0)
        say(f"  server state after set_voltage(0.0): {lab.server_state()}")
        assert lab.server_state()[f"{BIAS_PATH}#voltage"] == 0.0

        say("\n== Step 5: pulsing is allowed again ==")
        result = pulse_gen.pulse()
        say(f"  pulse_gen.pulse() -> {result!r}")
        assert result == "pulse #2 emitted"
        assert lab.pulse_gen.pulse_count == 2

        say("\n== Scenario complete: interlock enforced purely from recorded state ==")
    finally:
        lab.close()


def test_end_to_end_state_machine_interlock() -> None:
    """Pytest entry point — runs the scenario silently with assertions."""
    run_scenario()


if __name__ == "__main__":
    run_scenario(say=print)
