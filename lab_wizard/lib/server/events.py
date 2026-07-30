"""Append-only record of notable things a server did.

Once a workspace can change another workspace's config, "who added this
instrument, and when" stops being answerable from the YAML alone — git sees a
file change with no actor and no reason. This keeps a plain record beside the
config so that question has an answer.

Deliberately small. Events are appended as JSONL (greppable, survives restart,
diffable) and a bounded tail is kept in memory so a client can read recent
activity without parsing the file. There is no query language, no levels, no
rotation policy beyond a size cap — if this turns out to deserve a real UI, the
shape here is enough to build one on, and if it does not, nothing was wasted.

Not a substitute for logging. The application log is for diagnosing the server;
this is for explaining changes to a human looking at their rack later.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)

__all__ = ["EventLog", "Event"]

# Kept small: this is a human-readable tail, not a metrics pipeline.
DEFAULT_MEMORY_EVENTS = 200

# Beyond this the file is truncated to its most recent half. A lab machine can
# run for months, and an audit trail that fills a disk is worse than one that
# forgets its distant past.
DEFAULT_MAX_BYTES = 2_000_000


class Event(dict):
    """One recorded event. A dict so it serializes over the wire unchanged."""

    @classmethod
    def make(
        cls,
        kind: str,
        message: str,
        actor: Optional[str] = None,
        **details: Any,
    ) -> "Event":
        return cls(
            ts=time.time(),
            kind=kind,
            message=message,
            actor=actor or "unknown",
            details=details or {},
        )


class EventLog:
    """Bounded in-memory tail plus a JSONL file."""

    def __init__(
        self,
        path: Optional[Path | str] = None,
        *,
        memory_events: int = DEFAULT_MEMORY_EVENTS,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self._path = Path(path) if path else None
        self._recent: deque[Event] = deque(maxlen=memory_events)
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        if self._path is not None:
            self._load_tail()

    @property
    def path(self) -> Optional[Path]:
        return self._path

    def record(
        self, kind: str, message: str, actor: Optional[str] = None, **details: Any
    ) -> Event:
        """Append an event. Never raises — an audit note must not fail an operation."""
        event = Event.make(kind, message, actor, **details)
        with self._lock:
            self._recent.append(event)
            if self._path is not None:
                try:
                    self._path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self._path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(event) + "\n")
                    self._truncate_if_large()
                except OSError as exc:
                    logger.warning("Could not write event log: %s", exc)
        logger.info("[event] %s: %s (%s)", kind, message, event["actor"])
        return event

    def recent(self, limit: int = 50) -> list[Event]:
        """Most recent events, newest first."""
        with self._lock:
            items = list(self._recent)
        return list(reversed(items))[: max(0, limit)]

    # ------------------------- file handling -------------------------

    def _load_tail(self) -> None:
        """Seed the in-memory tail so a restart does not look like a fresh start."""
        assert self._path is not None
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()[-self._recent.maxlen :]
        except OSError as exc:
            logger.debug("Could not read event log: %s", exc)
            return
        for line in lines:
            try:
                self._recent.append(Event(json.loads(line)))
            except json.JSONDecodeError:
                continue

    def _truncate_if_large(self) -> None:
        """Drop the oldest half once the file passes the cap. Caller holds the lock."""
        assert self._path is not None
        try:
            if self._path.stat().st_size <= self._max_bytes:
                return
            with open(self._path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            keep = lines[len(lines) // 2 :]
            with open(self._path, "w", encoding="utf-8") as f:
                f.writelines(keep)
            logger.info("Truncated event log to %d most recent entries", len(keep))
        except OSError as exc:
            logger.debug("Could not truncate event log: %s", exc)
