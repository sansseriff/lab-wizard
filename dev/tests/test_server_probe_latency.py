"""Probing for an absent server must be quick.

`server_session` answers "does this workspace have a server?" by dialling each
local endpoint. A ZMQ `connect` succeeds even with nothing listening, so the
only way to know is to send something and wait — which means the *absence* of a
server is established by timing out, once per endpoint.

That makes the probe's cost the floor on every page that needs the answer. It
regressed once already: `Session` retries a timed-out call by default, which is
right for a working session whose server restarted and wrong for a liveness
probe, and it silently doubled the wall time. With two endpoints configured,
`/api/hardware-owner` took over three seconds whenever no server was running —
and because SvelteKit waits for a route's load before rendering, clicking
Instruments in the sidebar appeared to do nothing at all.

Nothing here asserts a specific implementation, only that asking the question
costs roughly one timeout per endpoint rather than two.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from lab_wizard.wizard.backend import hardware_access
from lab_wizard.wizard.backend.hardware_access import hardware_owner, server_session


def _endpoint_count(config_dir: Path) -> int:
    from lab_wizard.lib.client.server_discovery import local_endpoints

    return len(local_endpoints(config_dir))


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    """A config dir with a server bind configured but nothing listening."""
    config_dir = tmp_path / "config"
    (config_dir / "server").mkdir(parents=True)
    (config_dir / "server" / "server.yaml").write_text(
        "server:\n  bind: tcp://127.0.0.1:59137\n", encoding="utf-8"
    )
    return config_dir


def test_probe_does_not_retry_dead_endpoints(workspace: Path) -> None:
    """One timeout per endpoint, not two.

    The budget is the probe timeout per endpoint plus generous slack. A retry
    would double the true cost and blow it.
    """
    endpoints = _endpoint_count(workspace)
    assert endpoints >= 1

    budget_s = (hardware_access._PROBE_TIMEOUT_MS / 1000) * endpoints * 1.6

    start = time.perf_counter()
    with server_session(workspace) as session:
        assert session is None, "nothing is listening, so no session is expected"
    elapsed = time.perf_counter() - start

    assert elapsed < budget_s, (
        f"probing {endpoints} dead endpoint(s) took {elapsed:.2f}s, over the "
        f"{budget_s:.2f}s budget — a retry has probably crept back in"
    )


def test_hardware_owner_reports_wizard_without_a_server(workspace: Path) -> None:
    """The answer itself must stay correct, not merely fast."""
    start = time.perf_counter()
    result = hardware_owner(workspace)
    elapsed = time.perf_counter() - start

    assert result == {"owner": "wizard", "url": None}

    endpoints = _endpoint_count(workspace)
    budget_s = (hardware_access._PROBE_TIMEOUT_MS / 1000) * endpoints * 1.6
    assert elapsed < budget_s, (
        f"hardware_owner took {elapsed:.2f}s with no server running; this "
        "blocks page navigation in the wizard"
    )
