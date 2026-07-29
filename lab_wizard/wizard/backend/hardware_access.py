"""Route the wizard's hardware operations through the workspace server.

The wizard used to open instruments in its own process — `create_inst()` for
discovery, then `disconnect()` afterwards. With a server running that is two
processes fighting for one serial handle, and it also blinds the permission
gate: `StateTracker` only records what passes through the server, so anything
done in the GUI leaves the gate believing a stale picture of the hardware.

So: **if a server owns this workspace, it performs the operation.** The wizard
becomes one of its clients. In-process access remains only as the fallback for
when no server is running, which keeps the GUI usable on a workstation that has
never configured one.

Connection preference is ipc:// first (see
:mod:`lab_wizard.lib.client.server_discovery`) — lower latency than TCP
loopback, and unaffected by the bind address being reconfigured.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from lab_wizard.lib.client.server_discovery import local_endpoints
from lab_wizard.lib.client.session import Session


logger = logging.getLogger("lab_wizard.wizard.backend.hardware_access")

__all__ = ["server_session", "run_discovery", "hardware_owner"]

# Short: this is a liveness probe on a local socket, not a hardware operation.
_PROBE_TIMEOUT_MS = 800
# Discovery scans a whole GPIB bus and is legitimately slow.
_DISCOVERY_TIMEOUT_MS = 120_000


@contextmanager
def server_session(
    config_dir: str | Path, *, timeout_ms: int = _PROBE_TIMEOUT_MS
) -> Iterator[Optional[Session]]:
    """Yield a session to this workspace's server, or ``None`` if none answers.

    Tries each local endpoint in turn and verifies the connection with a cheap
    call — a ZMQ ``connect`` succeeds even with nothing listening, so the socket
    opening is not evidence that a server is there.
    """
    session: Optional[Session] = None
    for endpoint in local_endpoints(config_dir):
        candidate = Session(endpoint, timeout_ms=timeout_ms)
        try:
            candidate.call("list_paths")
            session = candidate
            logger.debug("Using instrument server at %s", endpoint)
            break
        except Exception as exc:  # noqa: BLE001 - no server here is normal
            logger.debug("No server at %s (%s)", endpoint, exc)
            candidate.close()

    try:
        yield session
    finally:
        if session is not None:
            session.close()


def hardware_owner(config_dir: str | Path) -> dict[str, Any]:
    """Who currently owns this workspace's hardware — for display."""
    with server_session(config_dir) as session:
        if session is None:
            return {"owner": "wizard", "url": None}
        return {"owner": "server", "url": session.url}


def run_discovery(
    config_dir: str | Path,
    *,
    type: str,
    action: str,
    params: dict[str, Any],
    parent_chain: list[dict[str, Any]],
    in_process_fallback: Any,
) -> dict[str, Any]:
    """Run a discovery action on whichever process owns the hardware.

    ``in_process_fallback`` is called only when no server answers. It is passed
    as a callable rather than inlined so the caller keeps its existing
    behaviour verbatim for the no-server case.
    """
    with server_session(config_dir, timeout_ms=_DISCOVERY_TIMEOUT_MS) as session:
        if session is not None:
            logger.info(
                "Delegating discovery %s/%s to server at %s", type, action, session.url
            )
            return session.call(
                "discover",
                {
                    "type": type,
                    "action": action,
                    "params": params,
                    "parent_chain": parent_chain,
                },
            )

    logger.info("No server running; discovering %s/%s in-process", type, action)
    return in_process_fallback()
