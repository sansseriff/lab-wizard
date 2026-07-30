"""Claims on exclusive transports, so a rack can be handed between processes.

Phase 1 could only *detect* a conflict and refuse. That is the right answer when
a server already holds a rack, but it leaves no way to say "I am taking this
bus for the next hour" — so two locally-run projects still collide with nothing
to stop them, and a server has no reason to keep its hands off.

A claim is a file naming the transport, its holder, and when it was taken:

    ~/.lab_wizard/leases/<transport-hash>.json
    {transport_key, pid, owner, acquired_at, note}

Keyed on the **transport**, not the config path, because that is the thing being
contended: two workspaces naming one serial port produce different root hashes
but the same ``serial:///dev/ttyUSB0``.

Claims are advisory, like everything else here — a stray pyvisa script ignores
them. They exist to make honest collisions legible, not to enforce anything
against a determined process. See
:mod:`lab_wizard.lib.instruments.general.transport`.

Liveness is by pid, reusing the reconciliation already proven in
``server_status``: a crashed holder's claim is reaped on the next read rather
than blocking the rack forever.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)

__all__ = [
    "lease_dir",
    "acquire",
    "release",
    "release_all_for_pid",
    "list_leases",
    "holder_of",
    "LeaseHeld",
]


class LeaseHeld(RuntimeError):
    """Raised when a transport is already claimed by a live process."""

    def __init__(self, transport_key: str, holder: dict[str, Any]) -> None:
        self.transport_key = transport_key
        self.holder = holder
        note = holder.get("note") or ""
        super().__init__(
            f"{transport_key} is claimed by {holder.get('owner', 'another process')} "
            f"(pid {holder.get('pid')}){f' — {note}' if note else ''}. "
            "Wait for it to finish, or stop that process."
        )


def lease_dir() -> Path:
    return Path(
        os.environ.get("LAB_WIZARD_LEASE_DIR")
        or Path.home() / ".lab_wizard" / "leases"
    )


def _path_for(transport_key: str) -> Path:
    digest = hashlib.sha256(transport_key.encode()).hexdigest()[:16]
    return lease_dir() / f"{digest}.json"


def _pid_alive(pid: int) -> bool:
    """Whether ``pid`` is a running process.

    ``EPERM`` means the process exists but belongs to someone else — a server
    running as another user, or under a launcher. Treating that as dead would
    reap a live holder's claim and hand its hardware to a second process, so
    only ``ESRCH`` counts as gone.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read(path: Path) -> Optional[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def holder_of(transport_key: str) -> Optional[dict[str, Any]]:
    """Live holder of ``transport_key``, reaping a dead one. ``None`` if free."""
    path = _path_for(transport_key)
    if not path.exists():
        return None
    entry = _read(path)
    pid = (entry or {}).get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        try:
            path.unlink(missing_ok=True)
            logger.info("Reaped stale lease on %s (pid %s gone)", transport_key, pid)
        except OSError:
            pass
        return None
    return entry


def acquire(
    transport_key: str,
    *,
    owner: str,
    note: str = "",
    pid: Optional[int] = None,
) -> dict[str, Any]:
    """Claim ``transport_key``, or raise :class:`LeaseHeld`.

    Claiming is atomic: the file is created with ``O_EXCL`` so two processes
    racing for the same rack cannot both believe they won. A dead holder's claim
    is reaped first, then the create is retried once.
    """
    existing = holder_of(transport_key)
    if existing is not None:
        if existing.get("pid") == (pid or os.getpid()):
            return existing  # already ours; re-acquiring is a no-op
        raise LeaseHeld(transport_key, existing)

    entry = {
        "transport_key": transport_key,
        "pid": pid if pid is not None else os.getpid(),
        "owner": owner,
        "acquired_at": time.time(),
        "note": note,
    }
    path = _path_for(transport_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # Someone claimed it between our check and here.
        current = holder_of(transport_key)
        raise LeaseHeld(transport_key, current or {"owner": "another process"})
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(entry, f)
    logger.info("Claimed %s for %s", transport_key, owner)
    return entry


def release(transport_key: str, *, pid: Optional[int] = None) -> bool:
    """Release our claim. Refuses to drop someone else's."""
    entry = holder_of(transport_key)
    if entry is None:
        return False
    if entry.get("pid") != (pid if pid is not None else os.getpid()):
        logger.warning(
            "Not releasing %s: held by pid %s, not us", transport_key, entry.get("pid")
        )
        return False
    try:
        _path_for(transport_key).unlink(missing_ok=True)
    except OSError:
        return False
    logger.info("Released %s", transport_key)
    return True


def release_all_for_pid(pid: Optional[int] = None) -> list[str]:
    """Release every claim held by ``pid``. Used on shutdown."""
    target = pid if pid is not None else os.getpid()
    released: list[str] = []
    for entry in list_leases():
        if entry.get("pid") == target and release(entry["transport_key"], pid=target):
            released.append(entry["transport_key"])
    return released


def list_leases() -> list[dict[str, Any]]:
    """Every live claim on this machine, reaping dead ones."""
    directory = lease_dir()
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        entry = _read(path)
        if entry is None:
            continue
        pid = entry.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        out.append(entry)
    out.sort(key=lambda e: e.get("acquired_at") or 0, reverse=True)
    return out
