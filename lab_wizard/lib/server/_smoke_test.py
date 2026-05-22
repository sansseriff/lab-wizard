"""End-to-end smoke test: server + RemoteExp + typed proxies, no hardware.

Covers both Phase 1 (raw wire RPCs) and Phase 2 (RemoteExp + typed proxies).

Run:
    python -m lab_wizard.lib.server._smoke_test
"""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource
from lab_wizard.lib.client.remote_exp import RemoteExp
from lab_wizard.lib.client.session import RemoteCallError
from lab_wizard.lib.instruments.general.vsense import StandInVSense, VSense
from lab_wizard.lib.instruments.general.vsource import StandInVSource, VSource
from lab_wizard.lib.server.demo_client import _send_request
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer


def _build_test_registry() -> InstrumentRegistry:
    """Construct a registry with stand-in instruments, bypassing the YAML walk."""
    reg = InstrumentRegistry.__new__(InstrumentRegistry)
    reg._index = {}
    reg._attribute_index = {}
    reg._index[f"{PATH_PREFIX}standin_vsource"] = StandInVSource()
    reg._index[f"{PATH_PREFIX}standin_vsense"] = StandInVSense()
    reg._attribute_index["bias"] = f"{PATH_PREFIX}standin_vsource"
    reg._attribute_index["sense"] = f"{PATH_PREFIX}standin_vsense"
    return reg


def main() -> int:
    bind = "tcp://127.0.0.1:12399"
    registry = _build_test_registry()
    server = WireServer(bind=bind, registry=registry)

    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    time.sleep(0.3)  # let ROUTER bind

    failures: list[str] = []

    def expect(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")
        else:
            print(f"  OK  {label} -> {got!r}")

    # ---------------- Phase 1: raw wire ----------------
    print("[1] raw wire: list_paths")
    r = _send_request(bind, "list_paths", None)
    expect(
        "list_paths",
        r.get("result"),
        [f"{PATH_PREFIX}standin_vsense", f"{PATH_PREFIX}standin_vsource"],
    )

    print("[2] raw wire: call set_voltage(0.5)")
    r = _send_request(
        bind,
        "call",
        {
            "path": f"{PATH_PREFIX}standin_vsource",
            "method": "set_voltage",
            "args": [0.5],
            "kwargs": {},
        },
    )
    expect("set_voltage(0.5)", r.get("result"), True)
    vsource = registry.resolve(f"{PATH_PREFIX}standin_vsource")
    expect("underlying voltage", vsource.voltage, 0.5)

    print("[3] raw wire: describe_attribute('bias')")
    r = _send_request(bind, "describe_attribute", {"name": "bias"})
    info = r.get("result")
    expect("describe_attribute behavior_abc", info.get("behavior_abc"), "VSource")
    expect("describe_attribute type_hint", info.get("type_hint"), "StandInVSource")

    # ---------------- Phase 2: typed proxies via RemoteExp ----------------
    print("[4] RemoteExp.from_attribute('bias') returns a VSource")
    exp = RemoteExp.connect(bind, timeout_ms=3000)
    try:
        bias = exp.from_attribute("bias")
        expect("isinstance(bias, RemoteVSource)", isinstance(bias, RemoteVSource), True)
        expect("isinstance(bias, VSource)", isinstance(bias, VSource), True)

        print("[5] RemoteVSource.set_voltage(1.25) round-trip")
        rv = bias.set_voltage(1.25)
        expect("set_voltage return", rv, True)
        expect("underlying voltage after proxy call", vsource.voltage, 1.25)

        print("[6] RemoteVSource.turn_on() round-trip")
        bias.turn_on()
        expect("underlying output_enabled", vsource.output_enabled, True)

        print("[7] RemoteExp.from_attribute('sense') returns a VSense")
        sense = exp.from_attribute("sense")
        expect("isinstance(sense, RemoteVSense)", isinstance(sense, RemoteVSense), True)
        expect("isinstance(sense, VSense)", isinstance(sense, VSense), True)

        print("[8] RemoteVSense.get_voltage() round-trip")
        registry.resolve(f"{PATH_PREFIX}standin_vsense").measurement_value = 2.345
        v = sense.get_voltage()
        expect("get_voltage", v, 2.345)

        print("[9] proxy caching: from_attribute returns same instance")
        bias2 = exp.from_attribute("bias")
        expect("proxy identity", bias is bias2, True)

        print("[10] reflective fallback: unknown method becomes remote call")
        # StandInVSource has no .nonexistent_method; should raise RemoteCallError
        try:
            bias.nonexistent_method()
        except RemoteCallError as exc:
            print(f"  OK  reflective unknown -> RemoteCallError({exc.code})")
        else:
            failures.append("expected RemoteCallError on unknown method via proxy")

        print("[11] error on unknown attribute name")
        try:
            exp.from_attribute("not_a_real_name")
        except RemoteCallError as exc:
            print(f"  OK  unknown attribute -> RemoteCallError({exc.code})")
        else:
            failures.append("expected RemoteCallError on unknown attribute")

        print("[12] list_attributes and list_descriptions")
        attrs = exp.list_attributes()
        expect("list_attributes", attrs, ["bias", "sense"])
        descs = exp.list_descriptions()
        expect("list_descriptions count", len(descs), 2)
        # Order of list_descriptions follows sorted attribute names
        expect("list_descriptions[0].behavior_abc", descs[0]["behavior_abc"], "VSource")
        expect("list_descriptions[1].behavior_abc", descs[1]["behavior_abc"], "VSense")
    finally:
        exp.close()

    # ---------------- Auto-forwarding regression check ----------------
    print("[13] auto-forwarder: brand-new ABC works without manual proxy methods")
    from abc import ABC, abstractmethod
    from typing import Type, cast

    from lab_wizard.lib.client.proxies.base import RemoteProxy

    class FakeBehavior(ABC):
        @abstractmethod
        def do_thing(self, x: float, y: float) -> float: ...

        @abstractmethod
        def reset(self) -> None: ...

    class FakeInstrument(FakeBehavior):
        def __init__(self) -> None:
            self.calls: list[tuple[Any, ...]] = []

        def do_thing(self, x: float, y: float) -> float:
            self.calls.append(("do_thing", x, y))
            return x * y

        def reset(self) -> None:
            self.calls.append(("reset",))

    # Register a fake instrument on the live server's registry.
    fake = FakeInstrument()
    registry._index[f"{PATH_PREFIX}fake"] = fake  # pyright: ignore[reportPrivateUsage]
    registry._attribute_index["fake"] = (
        f"{PATH_PREFIX}fake"  # pyright: ignore[reportPrivateUsage]
    )

    # Define the proxy in ONE LINE — no method bodies.
    class RemoteFake(FakeBehavior, RemoteProxy):
        pass

    # __init_subclass__ must have cleared the abstract set and injected
    # callable forwarders for every abstract method on the ABC.
    expect(
        "RemoteFake __abstractmethods__", RemoteFake.__abstractmethods__, frozenset()
    )
    expect("do_thing forwarder in class dict", "do_thing" in RemoteFake.__dict__, True)
    expect("reset forwarder in class dict", "reset" in RemoteFake.__dict__, True)
    expect("do_thing is callable", callable(RemoteFake.__dict__["do_thing"]), True)

    # Register the new proxy class so from_attribute(name, FakeBehavior)
    # resolves to it. In production this happens once at import time inside
    # proxies/registry.py; here we do it ad-hoc for the test and restore on
    # exit so we don't leak state into other tests.
    from lab_wizard.lib.client.proxies.registry import PROXY_BY_BEHAVIOR_ABC
    import lab_wizard.lib.server.registry as _server_reg

    PROXY_BY_BEHAVIOR_ABC["FakeBehavior"] = cast(Type[RemoteProxy], RemoteFake)
    _orig_behavior_abcs = _server_reg._BEHAVIOR_ABCS  # pyright: ignore[reportPrivateUsage]
    _server_reg._BEHAVIOR_ABCS = (  # pyright: ignore[reportPrivateUsage]
        ("FakeBehavior", FakeBehavior),
    ) + _orig_behavior_abcs

    exp2 = RemoteExp.connect(bind, timeout_ms=3000)
    try:
        # ---- The ergonomic demo ----
        # Pyright treats `fake_proxy` as `FakeInstrument`: ctrl-click on
        # `do_thing` jumps to FakeInstrument.do_thing, arguments are checked
        # against its signature, autocomplete shows its methods. The runtime
        # object is still a RemoteFake proxy — `as_type` is a static hint.
        fake_proxy = exp2.from_attribute("fake", FakeInstrument)
        result = fake_proxy.do_thing(3.0, 4.0)
        expect("typed-as FakeInstrument: do_thing", result, 12.0)
        expect("server-side recorded args", fake.calls[-1], ("do_thing", 3.0, 4.0))
        fake_proxy.reset()
        expect("typed-as FakeInstrument: reset", fake.calls[-1], ("reset",))

        # Calling a method that's NOT on FakeInstrument:
        # - pyright flags it at edit time (good — catches typos)
        # - runtime forwarder still tries it; server raises -> RemoteCallError
        try:
            fake_proxy.nonexistent()  # pyright: ignore[reportAttributeAccessIssue]
        except RemoteCallError:
            print(f"  OK  unknown method on FakeInstrument -> RemoteCallError")
        else:
            failures.append(
                "expected RemoteCallError for unknown method via auto-proxy"
            )

        # The static type is a lie; runtime type is the proxy.
        expect(
            "runtime isinstance RemoteFake (proxy)",
            isinstance(fake_proxy, RemoteFake),
            True,
        )
        expect(
            "runtime isinstance FakeInstrument (false — proxy is not the server class)",
            isinstance(fake_proxy, FakeInstrument),  # pyright: ignore[reportUnnecessaryIsInstance]
            False,
        )
    finally:
        exp2.close()
        _server_reg._BEHAVIOR_ABCS = _orig_behavior_abcs  # pyright: ignore[reportPrivateUsage]
        PROXY_BY_BEHAVIOR_ABC.pop("FakeBehavior", None)

    server.stop()
    server_thread.join(timeout=2)

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nAll smoke checks passed (Phase 1 + Phase 2 + auto-forwarding).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
