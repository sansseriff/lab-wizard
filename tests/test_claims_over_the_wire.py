"""Run claims end to end: a real server, real sockets, the real 53220A driver.

The acceptance test for ``plans/server_plan.md`` Phase 9 (9.13): two runs, each
holding one input of one simulated counter, interleaving counts at different
thresholds without either seeing the other's settings. Plus what makes that
trustworthy in practice: parallel dispatch, renewal, expiry, and the run
lifecycle claiming routed instruments on its own.
"""

from __future__ import annotations

import socket
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pytest

from lab_procedure import Status

from lab_wizard.lib.client.claims import RemoteClaim, RoutedClaims
from lab_wizard.lib.client.proxies.counter import RemoteCounter
from lab_wizard.lib.client.session import ClaimDeniedError, Session
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.utilities.config_io import save_instruments_to_config
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.lib.task_adapters.lifecycle import RunLifecycle

COUNTER = "inst://counter"
CH0, CH1 = f"{COUNTER}/channel/0", f"{COUNTER}/channel/1"
GATE_S = 0.05
PULSE_HEIGHT_MV = 200.0


class Slow:
    """Not an instrument: something that takes a while, on its own transport."""

    def work(self, seconds: float) -> bool:
        time.sleep(seconds)
        return True


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class LiveServer:
    def __init__(self, rig) -> None:
        self.rig = rig
        params = rig.counter_params()
        params.channels = {
                # Named, so the server lists them as attributes — which is how
                # the wizard's picker sees them.
                0: Keysight53220AChannelParams(attribute_name="counter_a"),
                1: Keysight53220AChannelParams(attribute_name="counter_b"),
        }
        # Written to a config dir as well, so the server serves the same tree the
        # wizard's picker reads.
        self.config_dir = Path(tempfile.mkdtemp()) / "config"
        save_instruments_to_config({"counter": params}, self.config_dir)
        self.registry = InstrumentRegistry.from_instruments({"counter": params})
        self.registry._index["inst://slow"] = Slow()
        self.url = f"tcp://127.0.0.1:{_free_port()}"
        # Short on purpose: an ipc path must fit in a sockaddr (~104 bytes on macOS).
        self.ipc_url = f"ipc:///tmp/lw-claims-{uuid.uuid4().hex[:8]}"
        self.server = WireServer(
            bind=[self.url, self.ipc_url], registry=self.registry, config_dir=str(self.config_dir)
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.sessions: list[Session] = []
        deadline = time.monotonic() + 5
        while True:  # wait for the bind rather than sleeping a guess
            try:
                self.session().call("list_paths")
                break
            except Exception:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.05)

    def session(self) -> Session:
        s = Session(self.url, timeout_ms=5000)
        self.sessions.append(s)
        return s

    def bias_the_detector(self) -> None:
        model = self.rig.model
        model.set_output_enabled(True)
        model.set_bias_voltage(0.026)  # on the plateau, below switching

    def close(self) -> None:
        for s in self.sessions:
            s.close()
        self.server.stop()
        self.thread.join(timeout=10)


@pytest.fixture
def live(rig) -> Iterator[LiveServer]:
    server = LiveServer(rig)
    try:
        yield server
    finally:
        server.close()


# --------------------------- 9.13: interleaving on one counter ---------------------------


def test_two_runs_interleave_on_the_two_inputs_of_one_counter(live: LiveServer):
    live.bias_the_detector()
    calibration_session, test_session = live.session(), live.session()
    calibration = RemoteCounter(calibration_session, CH0)
    test_detector = RemoteCounter(test_session, CH1)

    with RemoteClaim(live.url, [CH0], holder="calibration") as a, RemoteClaim(
        live.url, [CH1], holder="test detector"
    ) as b:
        calibration_session.claim_token, test_session.claim_token = a.token, b.token
        calibration.set_threshold(-50.0)  # counts every pulse
        test_detector.set_threshold(PULSE_HEIGHT_MV * 2)  # counts none

        calibration_counts, test_counts = [], []
        for _ in range(3):
            calibration_counts.append(calibration.count(GATE_S))
            test_counts.append(test_detector.count(GATE_S))

    # Had either run's threshold leaked into the other's count, one of these
    # would be the other's answer.
    assert all(c > 1000 for c in calibration_counts), calibration_counts
    assert test_counts == [0, 0, 0]


def test_the_refusals_that_make_interleaving_safe(live: LiveServer):
    holder_session, stranger = live.session(), live.session()
    with RemoteClaim(live.url, [CH0], holder="calibration") as claim:
        holder_session.claim_token = claim.token

        with pytest.raises(ClaimDeniedError, match="claimed by calibration"):
            RemoteClaim(live.url, [COUNTER], holder="whole counter").__enter__()
        with pytest.raises(ClaimDeniedError, match="but not inst://counter"):
            holder_session.call_inst(COUNTER, "configure_gate", kwargs={"source": "input2"})
        with pytest.raises(ClaimDeniedError, match="claimed by calibration"):
            stranger.call_inst(CH0, "set_threshold", [30.0])
        # Reads stay open to anyone.
        assert stranger.call_inst(CH0, "get_gate_time") == pytest.approx(1.0)


# --------------------------- parallel dispatch ---------------------------


def test_a_long_call_does_not_block_other_clients(live: LiveServer):
    slow_session, other = live.session(), live.session()
    worker = threading.Thread(target=slow_session.call_inst, args=("inst://slow", "work", [1.5]))
    worker.start()
    time.sleep(0.2)  # the slow call is now in flight on the server

    started = time.monotonic()
    other.call("claim_list")
    other.call_inst(CH0, "get_gate_time")
    elapsed = time.monotonic() - started
    worker.join()

    assert elapsed < 0.75, f"a fast request waited {elapsed:.2f}s behind a slow one"


def test_a_claim_outlives_its_ttl_while_renewed_and_lapses_when_not(live: LiveServer):
    session = live.session()
    with RemoteClaim(live.url, [CH1], holder="renewed", ttl_s=0.4) as claim:
        session.claim_token = claim.token
        time.sleep(1.2)  # three TTLs
        assert session.call_inst(CH1, "set_threshold", [30.0]) is True
        assert not claim.lost.is_set()

    # A claim nobody renews, as after a client crash.
    token = live.session().call("claim_acquire", {"paths": [CH0], "holder": "crashed", "ttl_s": 0.3})["token"]
    session.claim_token = token
    time.sleep(0.8)
    with pytest.raises(ClaimDeniedError, match="no longer held"):
        session.call_inst(CH0, "set_threshold", [30.0])


# --------------------------- the lifecycle claims routed instruments ---------------------------


def test_a_run_holds_its_routed_instruments_and_hands_them_back_at_baseline(live: LiveServer):
    session, observer = live.session(), live.session()

    @dataclass
    class Resources:
        counter: RemoteCounter

    seen_during_run: list[list[dict]] = []

    def execute(resources: Resources) -> Status:
        seen_during_run.append(observer.call("claim_list"))
        resources.counter.set_threshold(PULSE_HEIGHT_MV * 2)
        return Status.SUCCESS

    status = RunLifecycle(
        claims_after_resolve=[lambda instruments: RoutedClaims(instruments, holder="pcr_run")]
    ).run(resolve=lambda: Resources(counter=RemoteCounter(session, CH0)), execute=execute)

    assert status is Status.SUCCESS
    assert [c["holder"] for c in seen_during_run[0]] == ["pcr_run"]
    assert session.claim_token is None

    deadline = time.monotonic() + 5  # the baseline restore runs on a server worker
    while observer.call("claim_list") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert observer.call("claim_list") == []
    assert observer.call_inst(CH0, "get_threshold") == pytest.approx(-50.0)


# --------------------------- the wizard's view ---------------------------


def test_the_wizard_lists_claims_and_force_releases_only_from_this_machine(live: LiveServer, monkeypatch):
    from fastapi.testclient import TestClient

    import lab_wizard.lib.client.server_registry as server_registry
    from lab_wizard.wizard.backend.main import app

    entry = {"pid": 4242, "workspace_path": "/labs/cryo-a", "bind": live.url, "ipc": None}
    monkeypatch.setattr(server_registry, "list_local_servers", lambda: [entry])
    monkeypatch.setattr(server_registry, "local_server_endpoints", lambda _e: [live.url])

    token = live.session().call("claim_acquire", {"paths": [CH0], "holder": "stuck run"})["token"]
    client = TestClient(app)

    listed = client.get("/api/local-servers/claims").json()["servers"]
    assert [c["holder"] for c in listed[0]["claims"]] == ["stuck run"]
    assert token not in str(listed)

    # Over tcp the server cannot tell this machine from another, so it refuses.
    refused = client.post("/api/local-servers/claims/force-release", json={"url": live.url, "unit": COUNTER})
    assert refused.status_code == 400
    assert "only available to clients on this machine" in refused.json()["detail"]

    # Over ipc — how the wizard actually reaches a server on this machine — it works.
    released = client.post(
        "/api/local-servers/claims/force-release", json={"url": live.ipc_url, "unit": COUNTER}
    )
    assert released.status_code == 200
    assert released.json()["released"] == [CH0]


def test_the_picker_is_told_which_instruments_a_run_is_holding(live: LiveServer, tmp_path, monkeypatch):
    """A claimed instrument shows as busy where it is chosen (server_plan 9.10).

    The picker still offers it — a claim may well be over before the new project
    runs — so this is an annotation, not a filter. The claim is on one input, so
    only that input and the box above it are marked; the other input is free.
    """
    from lab_wizard.wizard.backend import instrument_sources as sources_mod
    from lab_wizard.wizard.backend.instrument_sources import list_instrument_sources

    config_dir = tmp_path / "config"
    (config_dir / "instruments").mkdir(parents=True)
    entry = {
        "pid": 4242,
        "workspace_path": str(tmp_path),
        "config_dir": str(config_dir),
        "bind": live.url,
        "ipc": live.ipc_url,
    }
    # Over ipc, as the wizard actually reaches a server on this machine: reading
    # the tree is refused to clients that could be on another machine.
    monkeypatch.setattr(sources_mod, "list_local_servers", lambda: [entry])
    monkeypatch.setattr(sources_mod, "local_server_endpoints", lambda _e: [live.ipc_url])

    free = list_instrument_sources(str(config_dir))
    own = next(s for s in free["sources"] if s.get("is_own_server"))
    assert own["claims"] == []
    assert all(a.get("claimed_by") is None for a in own["attributes"])

    live.session().call("claim_acquire", {"paths": [CH0], "holder": "pcr_run"})

    held = list_instrument_sources(str(config_dir))
    own = next(s for s in held["sources"] if s.get("is_own_server"))
    assert [c["holder"] for c in own["claims"]] == ["pcr_run"]
    by_path = {a["path"]: a.get("claimed_by") for a in own["attributes"]}
    assert by_path[CH0] == "pcr_run"
    assert by_path[CH1] is None

    # The local tree is the same hardware, so it carries the same answer.
    local = next(s for s in held["sources"] if s["kind"] == "local")
    assert [c["holder"] for c in local["claims"]] == ["pcr_run"]


# --------------------------- the call log ---------------------------


def test_the_server_lists_calls_in_flight_and_how_recent_ones_ended(live: LiveServer):
    session, watcher = live.session(), live.session()
    worker = threading.Thread(target=lambda: session.call("call", {"path": "inst://slow", "method": "work", "args": [0.5]}))
    worker.start()
    deadline = time.monotonic() + 3
    while not (active := watcher.call("calls_recent")["active"]):
        assert time.monotonic() < deadline, "the slow call never showed as in flight"
        time.sleep(0.02)
    assert (active[0]["path"], active[0]["method"], active[0]["args"]) == ("inst://slow", "work", "0.5")
    worker.join()

    with pytest.raises(Exception):
        session.call("call", {"path": "inst://slow", "method": "nope"})
    with RemoteClaim(live.url, [CH0], holder="calibration") as claim:
        session.claim_token = claim.token
        RemoteCounter(session, CH0).set_threshold(-50.0)

    calls = watcher.call("calls_recent")
    assert calls["active"] == []
    newest, refused, slow = calls["recent"][:3]
    assert (newest["method"], newest["holder"], newest["ok"]) == ("set_threshold", "calibration", True)
    assert refused["ok"] is False and "nope" in refused["error"]
    assert slow["ok"] is True and slow["duration_ms"] >= 500
    assert calls["total"] >= 3
