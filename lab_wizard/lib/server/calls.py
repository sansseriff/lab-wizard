"""The instrument calls a server has routed, for a person to glance at.

"What is going through the server right now?" is the question: which run is
driving which instrument, whether anything is stuck, what was refused. The
answer is the calls in flight plus a short tail of finished ones.

In memory only, unlike the event log (:mod:`events`). A sweep makes thousands of
calls; they explain nothing a week later and would bury the changes the event
log exists to record. A restarted server starts with an empty list.
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Any, Iterator, Optional

__all__ = ["CallLog"]

# Enough to see what a run has been doing over the last few seconds of a sweep.
DEFAULT_SIZE = 200
# An argument is shown, not reproduced: a waveform is not worth its bytes here.
_ARG_CHARS = 40
_ERROR_CHARS = 200


def _short(value: Any) -> str:
    text = repr(value)
    return text if len(text) <= _ARG_CHARS else text[: _ARG_CHARS - 1] + "…"


def describe_args(args: list[Any], kwargs: dict[str, Any]) -> str:
    """``0.25, gate_s=0.1`` — the arguments as a person reads a call."""
    parts = [_short(a) for a in args] + [f"{k}={_short(v)}" for k, v in kwargs.items()]
    return ", ".join(parts)


class CallLog:
    """Calls in flight, and the most recent finished ones."""

    def __init__(self, size: int = DEFAULT_SIZE) -> None:
        self._recent: deque[dict[str, Any]] = deque(maxlen=size)
        self._active: dict[int, dict[str, Any]] = {}
        self._ids = itertools.count(1)
        self._total = 0
        self._started = time.time()
        self._lock = threading.Lock()

    @contextmanager
    def record(
        self,
        *,
        path: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
        caller: Optional[str],
        holder: Optional[str],
    ) -> Iterator[None]:
        """Log one call for as long as it runs, and how it ended."""
        entry: dict[str, Any] = {
            "id": next(self._ids),
            "started": time.time(),
            "path": path,
            "method": method,
            "args": describe_args(args, kwargs),
            "caller": caller,
            "holder": holder,
        }
        with self._lock:
            self._active[entry["id"]] = entry
        clock = time.perf_counter()
        try:
            yield
            entry["ok"], entry["error"] = True, None
        except BaseException as exc:
            entry["ok"] = False
            entry["error"] = (str(exc) or type(exc).__name__)[:_ERROR_CHARS]
            raise
        finally:
            entry["duration_ms"] = round((time.perf_counter() - clock) * 1000, 1)
            with self._lock:
                self._active.pop(entry["id"], None)
                self._recent.append(entry)
                self._total += 1

    def snapshot(self, limit: int = 50) -> dict[str, Any]:
        """In flight (oldest first) and finished (newest first), with a running total."""
        now = time.time()
        with self._lock:
            active = [
                {**e, "elapsed_ms": round((now - e["started"]) * 1000, 1)}
                for e in sorted(self._active.values(), key=lambda e: e["started"])
            ]
            recent = list(reversed(self._recent))[: max(0, limit)]
            total = self._total
        return {"active": active, "recent": recent, "total": total, "since": self._started}
