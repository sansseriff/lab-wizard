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

__all__ = ["server_session", "run_discovery", "hardware_owner", "apply_tree_edit"]

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

    **Liveness is always probed quickly, whatever ``timeout_ms`` says.** A
    session's receive timeout is fixed when its socket is created, so probing
    with the working timeout meant "is anyone there?" inherited the patience
    needed for the work itself: a discovery scan on a machine with no server
    sat for two minutes per endpoint before falling back in-process, which
    reads as a hung Discover button. The probe answers in under a second; only
    once something has answered is a session opened at the real timeout.
    """
    session: Optional[Session] = None
    probe_ms = min(_PROBE_TIMEOUT_MS, timeout_ms)
    for endpoint in local_endpoints(config_dir):
        # `auto_reconnect` retries once on timeout, which is right for a working
        # session whose server restarted underneath it — and exactly wrong for a
        # liveness probe, whose entire job is to answer "is anyone there?"
        # quickly. Left on, every dead endpoint costs *twice* the probe timeout,
        # and a workspace with no server pays that for each endpoint before any
        # page depending on this can render.
        probe = Session(endpoint, timeout_ms=probe_ms, auto_reconnect=False)
        try:
            probe.call("list_paths")
        except Exception as exc:  # noqa: BLE001 - no server here is normal
            logger.debug("No server at %s (%s)", endpoint, exc)
            probe.close()
            continue

        # The probe answered, but it has retries disabled, so it is not the
        # session to hand back for real work.
        logger.debug("Using instrument server at %s", endpoint)
        probe.close()
        session = Session(endpoint, timeout_ms=timeout_ms)
        break

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


_TREE_EDIT_TIMEOUT_MS = 20_000

_TREE_RPCS = {"add": "tree_add", "remove": "tree_remove", "reset": "tree_reset"}


def apply_tree_edit(
    config_dir: str | Path,
    operation: str,
    payload: dict[str, Any],
    in_process_fallback: Any,
) -> dict[str, Any]:
    """Apply a config change through this workspace's server when one is running.

    Editing our *own* tree used to write YAML directly while editing another
    workspace's went through ``tree_*``, so the local path — the one used far
    more often — was the worse-behaved of the two. It skipped the held-rack
    refusal, left the running server's registry stale until someone restarted
    it, and recorded nothing in the audit log. Routing both through the same
    RPC removes the asymmetry rather than duplicating the checks here.

    Falls back to writing directly when no server answers, which is the ordinary
    case for a workspace that has not opted into hosting.
    """
    rpc = _TREE_RPCS.get(operation)
    if rpc is None:
        raise ValueError(f"Unknown tree operation {operation!r}")

    with server_session(config_dir, timeout_ms=_TREE_EDIT_TIMEOUT_MS) as session:
        if session is not None:
            logger.info("Applying %s through the server at %s", operation, session.url)
            return session.call(rpc, payload)

    logger.info("No server running; applying %s in-process", operation)
    return in_process_fallback()


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
