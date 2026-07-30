"""Who is making the current request, and over which transport.

Authority here is decided by **how a client connected**, not by anything it
claims. Reaching the ``ipc://`` socket requires filesystem access to it, which
only a process on this machine can have — so "arrived on ipc" is a
same-machine proof enforced by the OS rather than an assertion in a payload.

That gives a rule that is easy to state and hard to get wrong:

    ipc://  — same machine. May read, call, and edit the instrument tree.
    tcp://  — another machine (or a same-machine client that chose tcp).
              May read and call already-configured instruments. No
              reconfiguration.

A same-machine client connecting over tcp deliberately gets the smaller set.
The privilege is then explicit — you opt into it by using the local socket —
rather than ambient and accidental.

The current peer is held in a :class:`~contextvars.ContextVar` set just before
JSON-RPC dispatch, so RPC methods can consult it without every signature
growing a parameter.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional


__all__ = [
    "Peer",
    "current_peer",
    "set_current_peer",
    "reset_current_peer",
    "require_local",
    "LocalOnlyError",
]


@dataclass(frozen=True)
class Peer:
    """The caller of the request being handled."""

    # "ipc" | "tcp" — which socket the request arrived on.
    transport: str
    # ZMQ ROUTER peer identity; opaque, but stable for one connection.
    identity: str
    # Client-supplied name from the message envelope. Useful for the audit
    # trail, never for authority — a client picks its own.
    name: Optional[str] = None

    @property
    def is_local(self) -> bool:
        return self.transport == "ipc"

    def describe(self) -> str:
        """Short label for the audit log."""
        if self.name:
            return f"{self.name} ({self.transport})"
        return f"{self.transport}:{self.identity[:12]}"


class LocalOnlyError(PermissionError):
    """Raised when a remote peer attempts a same-machine-only operation."""

    def __init__(self, operation: str) -> None:
        super().__init__(
            f"{operation} is only available to clients on this machine. "
            "Remote clients may read the instrument tree and call configured "
            "instruments, but not reconfigure them."
        )


_CURRENT: contextvars.ContextVar[Optional[Peer]] = contextvars.ContextVar(
    "lab_wizard_current_peer", default=None
)


def current_peer() -> Optional[Peer]:
    """Peer for the request being handled, if dispatch set one."""
    return _CURRENT.get()


def set_current_peer(peer: Optional[Peer]) -> contextvars.Token:
    return _CURRENT.set(peer)


def reset_current_peer(token: contextvars.Token) -> None:
    """Restore the previous peer. Paired with :func:`set_current_peer`."""
    _CURRENT.reset(token)


def require_local(operation: str) -> Peer:
    """Assert the caller is on this machine, returning it.

    Used to gate reconfiguration. A request with no peer at all is treated as
    local: that is the in-process path (tests, the single-project host), which
    is by definition running on this machine.
    """
    peer = current_peer()
    if peer is None:
        return Peer(transport="ipc", identity="in-process", name="in-process")
    if not peer.is_local:
        raise LocalOnlyError(operation)
    return peer
