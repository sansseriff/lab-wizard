"""Check whether a local project may open its hardware itself.

A project generated for *local* control opens instruments in its own process.
That is fine — until this workstation's server holds the same hardware, at
which point the two contend for one serial handle and the symptom is an opaque
VISA/serial error from somewhere deep in a driver.

This runs one cheap query before any hardware is touched and turns that into a
sentence naming the rack and what to do about it.

Only **exclusive** roots can conflict. A root behind a server that already
multiplexes (a DBay rack in GUI mode, say) is shared by design: a local project
and the lab_wizard server can both talk to it, and it is skipped here. See
:mod:`lab_wizard.lib.instruments.general.transport`.

Nothing here infers "recent use" — the server is asked what it *holds*, which
is exact and explainable. A running server that has not resolved a path holds
nothing, so simply having one up does not block local work.
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

from lab_wizard.lib.server.registry import PATH_PREFIX, root_path


logger = logging.getLogger(__name__)

__all__ = [
    "ConflictError",
    "Conflict",
    "conflicts_for_roots",
    "preflight_local_project",
    "project_root_keys",
    "project_transport_keys",
]


class Conflict:
    """One root a local project wants that the server already holds."""

    def __init__(self, root: str, transport_key: Optional[str], held_paths: list[str]):
        self.root = root
        self.transport_key = transport_key
        self.held_paths = held_paths

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Conflict {self.root} key={self.transport_key!r}>"

    def describe(self) -> str:
        where = self.transport_key or self.root
        return f"  {self.root} ({where}) — {len(self.held_paths)} instrument(s) in use"


class ConflictError(RuntimeError):
    """Raised when a local project would fight the server for hardware."""

    def __init__(self, conflicts: list[Conflict], url: str):
        self.conflicts = conflicts
        self.url = url
        detail = "\n".join(c.describe() for c in conflicts)
        super().__init__(
            "This project opens instruments directly, but the instrument server "
            f"at {url} already has them open:\n"
            f"{detail}\n\n"
            "One process at a time can hold these transports. Either stop the "
            "server, or regenerate this project to run through it."
        )


def project_root_keys(instruments: dict[str, Any]) -> set[str]:
    """Root ``inst://`` paths a project's instrument tree will open.

    Taken from the project YAML's own keys, which are the same hashes the
    server addresses by — so this needs no hardware and no server.
    """
    return {f"{PATH_PREFIX}{key}" for key in instruments}


def project_transport_keys(instruments: dict[str, Any]) -> dict[str, Optional[str]]:
    """``{root_path: transport_key}`` for a project's roots.

    The transport key is what makes cross-workspace detection work. Two
    workspaces configuring one serial port produce *different* root hashes, so
    comparing roots alone would miss the collision entirely; both resolve to the
    same ``serial:///dev/ttyUSB0``.
    """
    out: dict[str, Optional[str]] = {}
    for key, params in instruments.items():
        root = f"{PATH_PREFIX}{key}"
        getter = getattr(params, "transport_key", None)
        try:
            out[root] = getter() if callable(getter) else None
        except Exception:  # noqa: BLE001 - never block a run on this
            out[root] = None
    return out


def conflicts_for_roots(
    wanted_roots: Iterable[str],
    held: dict[str, Any],
    wanted_transport_keys: Optional[dict[str, Optional[str]]] = None,
) -> list[Conflict]:
    """Intersect what a project wants with what a server holds.

    Matching is by root path *or* transport key. The path catches the
    same-workspace case; the key catches the same device reached through a
    different config — another workspace's server, or this workspace having
    configured the device twice.

    Shared roots are dropped: something else already multiplexes them, so
    concurrent use is the design rather than a conflict.
    """
    exclusive = held.get("exclusive_roots") or {}
    held_roots = set(held.get("held_roots") or [])
    held_paths = held.get("held_paths") or []
    keys = wanted_transport_keys or {}

    # Transport keys the server holds *and* cannot share.
    held_keys: dict[str, str] = {}
    for root, info in exclusive.items():
        if root not in held_roots:
            continue
        key = (info or {}).get("transport_key")
        if key:
            held_keys[key] = root

    out: list[Conflict] = []
    for root in sorted(set(wanted_roots)):
        wanted_key = keys.get(root)

        if root in held_roots and root in exclusive:
            blocking_root = root
        elif wanted_key and wanted_key in held_keys:
            # Same hardware, different config entry.
            blocking_root = held_keys[wanted_key]
        else:
            continue

        out.append(
            Conflict(
                root=blocking_root,
                transport_key=(exclusive.get(blocking_root) or {}).get("transport_key"),
                held_paths=[p for p in held_paths if root_path(p) == blocking_root],
            )
        )
    return out


def _ask_held(url: str, timeout_ms: int) -> Optional[dict[str, Any]]:
    """``list_held`` from one server, or ``None`` if it does not answer."""
    from lab_wizard.lib.client.session import Session

    try:
        session = Session(url, timeout_ms=timeout_ms)
        try:
            return session.call("list_held") or {}
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - absence of a server is normal
        logger.debug("No reachable server at %s (%s)", url, exc)
        return None


def preflight_local_project(
    instruments: dict[str, Any],
    url: Optional[str] = None,
    *,
    timeout_ms: int = 2000,
    include_machine_servers: bool = True,
) -> None:
    """Raise :class:`ConflictError` if any local server holds hardware we want.

    Every instrument server on this machine is consulted, not only this
    workspace's. A server started from another workspace holds real hardware,
    and its endpoint is not derivable from here — checking only our own
    workspace would sail straight into the collision this exists to prevent.
    Contention is compared by root, and cross-workspace overlap is caught by
    ``transport_key``: two configs naming one serial port resolve to the same
    key even under different root hashes.

    ``url`` optionally adds an explicit server (a remote one, say). An
    unreachable server is not an error — local projects must still run when
    nothing is up.
    """
    wanted = project_root_keys(instruments)
    if not wanted:
        return
    wanted_keys = project_transport_keys(instruments)

    candidates: list[str] = []
    if include_machine_servers:
        from lab_wizard.lib.client.server_registry import (
            list_local_servers,
            local_server_endpoints,
        )

        for entry in list_local_servers():
            endpoints = local_server_endpoints(entry)
            if endpoints:
                candidates.append(endpoints[0])
    if url:
        candidates.append(url)

    for endpoint in dict.fromkeys(candidates):  # de-dup, preserve order
        held = _ask_held(endpoint, timeout_ms)
        if held is None:
            continue
        conflicts = conflicts_for_roots(wanted, held, wanted_keys)
        if conflicts:
            raise ConflictError(conflicts, endpoint)
