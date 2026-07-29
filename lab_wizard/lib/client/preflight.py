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


def conflicts_for_roots(
    wanted_roots: Iterable[str], held: dict[str, Any]
) -> list[Conflict]:
    """Intersect the roots a project wants with what the server holds.

    ``held`` is the server's ``list_held`` reply. Shared roots are dropped:
    they are multiplexed by something else and both processes may use them.
    """
    exclusive = held.get("exclusive_roots") or {}
    held_roots = set(held.get("held_roots") or [])
    held_paths = held.get("held_paths") or []

    out: list[Conflict] = []
    for root in sorted(set(wanted_roots)):
        if root not in held_roots:
            continue  # server may serve it, but has not opened it
        if root not in exclusive:
            continue  # shared transport — concurrent use is the design
        out.append(
            Conflict(
                root=root,
                transport_key=(exclusive.get(root) or {}).get("transport_key"),
                held_paths=[p for p in held_paths if root_path(p) == root],
            )
        )
    return out


def preflight_local_project(
    instruments: dict[str, Any],
    url: Optional[str],
    *,
    timeout_ms: int = 2000,
) -> None:
    """Raise :class:`ConflictError` if the server holds hardware we want.

    ``url`` is this workstation's server. Passing ``None`` skips the check.
    An unreachable server is *not* an error: the point is to catch a running
    server, and a local project must still work when none is up.
    """
    if not url:
        return

    wanted = project_root_keys(instruments)
    if not wanted:
        return

    from lab_wizard.lib.client.session import Session

    try:
        session = Session(url, timeout_ms=timeout_ms)
        try:
            held = session.call("list_held")
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - absence of a server is normal
        logger.debug("No reachable server at %s (%s); skipping preflight", url, exc)
        return

    conflicts = conflicts_for_roots(wanted, held or {})
    if conflicts:
        raise ConflictError(conflicts, url)
