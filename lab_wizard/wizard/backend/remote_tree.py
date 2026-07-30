"""View and edit another workspace's instrument tree.

Cross-workspace discovery was useful only for looking at a list of servers; this
is what makes it operational. A server on this machine can be pointed at, its
tree rendered with the *server's* schema, and — because it is the same machine —
edited.

Two things are deliberately not mirrored locally:

* **The tree.** The server owns its config. Nothing is copied into this
  workspace, so there is no second copy to drift.
* **The schema.** Which instrument classes exist, and what fields they take,
  depend on the lab_wizard build running *there*. Rendering a form from our own
  metadata would let a user configure something the server cannot instantiate.

Editing is same-machine only, enforced by the server (see
:mod:`lab_wizard.lib.server.peer`) rather than trusted here: this module simply
connects over the endpoint the registry advertised, and the server decides.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from lab_wizard.lib.client.server_registry import (
    list_local_servers,
    local_server_endpoints,
)
from lab_wizard.lib.client.session import Session


logger = logging.getLogger("lab_wizard.wizard.backend.remote_tree")

__all__ = ["remote_tree", "remote_schema", "remote_events", "remote_tree_edit"]

_TIMEOUT_MS = 15_000


def _endpoint_for(config_dir: str) -> str:
    """Best endpoint for the server hosting ``config_dir``.

    Resolved through the machine registry rather than taken from the caller, so
    a client cannot be talked into dialling something else — and so ipc is
    preferred, which is what grants edit rights.
    """
    for entry in list_local_servers():
        if entry.get("config_dir") == config_dir:
            endpoints = local_server_endpoints(entry)
            if endpoints:
                return endpoints[0]
            raise ValueError(f"Server for {config_dir} advertises no endpoint.")
    raise ValueError(
        f"No running server found for {config_dir}. It may have stopped since "
        "the list was loaded."
    )


@contextmanager
def _session(config_dir: str) -> Iterator[Session]:
    session = Session(_endpoint_for(config_dir), timeout_ms=_TIMEOUT_MS)
    try:
        yield session
    finally:
        session.close()


def remote_tree(config_dir: str) -> dict[str, Any]:
    """The tree, schema and recent activity of the server hosting ``config_dir``.

    Fetched together so the UI renders one coherent picture: a tree drawn with a
    schema from a different fetch could disagree with itself.
    """
    with _session(config_dir) as session:
        tree = session.call("tree_get")
        schema = session.call("schema_get")
        try:
            events = session.call("events_recent", {"limit": 20})
        except Exception:  # noqa: BLE001 - older servers have no event log
            events = []
        try:
            config = session.call("config_status")
        except Exception:  # noqa: BLE001
            config = {}
    return {
        "tree": tree.get("tree", []),
        "roots": tree.get("roots", {}),
        "held_roots": tree.get("held_roots", []),
        "config_dir": tree.get("config_dir"),
        "metadata": schema.get("instrument_metadata", {}),
        "permission_vocabulary": schema.get("permission_vocabulary", []),
        "events": events,
        "config_status": config,
    }


def remote_schema(config_dir: str) -> dict[str, Any]:
    with _session(config_dir) as session:
        return session.call("schema_get")


def remote_events(config_dir: str, limit: int = 50) -> list[dict[str, Any]]:
    with _session(config_dir) as session:
        return session.call("events_recent", {"limit": limit})


def remote_tree_edit(
    config_dir: str, operation: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """Apply a tree change on the server hosting ``config_dir``.

    ``operation`` is ``add`` / ``remove`` / ``reset``. The server refuses if the
    caller is remote, or if the rack is currently open — both are its decisions,
    surfaced here as an error the UI can show.
    """
    rpc = {"add": "tree_add", "remove": "tree_remove", "reset": "tree_reset"}.get(
        operation
    )
    if rpc is None:
        raise ValueError(f"Unknown tree operation {operation!r}")

    with _session(config_dir) as session:
        result = session.call(rpc, payload)
    logger.info("Applied %s on the server hosting %s", operation, config_dir)
    return result
