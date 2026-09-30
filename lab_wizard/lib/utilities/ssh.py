"""Whether this process was started over SSH."""

from __future__ import annotations

import os

__all__ = ["is_ssh_session"]


def is_ssh_session() -> bool:
    """Heuristic: SSH sets one of these in the sessions it starts."""
    return any(os.environ.get(var) for var in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"))
