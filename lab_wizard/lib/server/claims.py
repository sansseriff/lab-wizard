"""Run-scoped claims: which run may write to which part of the instrument tree.

Everything else on the server decides *which process* owns hardware. This decides
*which run* may drive an instrument once several clients share one server. See
``plans/server_plan.md`` Phase 9 for the rules; in short:

1. A claim on a path covers that path and everything under it.
2. Claims held by different runs never overlap — an ancestor and a descendant
   exclude each other, siblings coexist.
3. A write to a path that any claim touches requires a claim *covering* it.
   Holding channel 0 does not let a run change the counter's shared trigger.
4. Pure queries need nothing.

Claims expire unless renewed, so a crashed client does not hold hardware forever.
A released or expired claim does not free its units at once: the server first
restores them to their configured baseline, and until that finishes they stay
unavailable. Otherwise the next holder's own settings could be overwritten by a
reset that happened to run after it started.

This module is the table and its rules only. It opens no hardware, takes no
transport lock, and knows nothing about the wire; ``WireServer`` does those.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field


__all__ = ["Claim", "ClaimConflict", "ClaimTable", "covers", "overlaps"]


def covers(unit: str, path: str) -> bool:
    """Whether a claim on ``unit`` covers ``path``."""
    return path == unit or path.startswith(unit + "/")


def overlaps(a: str, b: str) -> bool:
    """Whether claims on ``a`` and ``b`` would contend: one covers the other."""
    return covers(a, b) or covers(b, a)


@dataclass
class Claim:
    token: str
    holder: str
    peer: str
    units: tuple[str, ...]
    ttl_s: float
    acquired_at: float
    deadline: float
    # True once released or expired, while its units are reset to baseline.
    restoring: bool = field(default=False)

    def covers(self, path: str) -> bool:
        return any(covers(unit, path) for unit in self.units)

    def overlaps(self, path: str) -> bool:
        return any(overlaps(unit, path) for unit in self.units)

    def to_wire(self, now: float) -> dict:
        return {
            "holder": self.holder,
            "peer": self.peer,
            "units": list(self.units),
            "ttl_s": self.ttl_s,
            "acquired_at": self.acquired_at,
            "expires_in_s": None if self.restoring else max(0.0, self.deadline - now),
            "restoring": self.restoring,
        }


class ClaimConflict(RuntimeError):
    """A requested unit overlaps one another run holds."""

    def __init__(self, unit: str, holder: Claim) -> None:
        self.unit = unit
        self.holder = holder
        doing = "is being reset to baseline after" if holder.restoring else "is claimed by"
        super().__init__(f"{unit} {doing} {holder.holder}")


class ClaimTable:
    """Thread-safe claim bookkeeping. ``clock`` is monotonic seconds."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], float] = time.time,
    ) -> None:
        self._clock = clock
        self._wallclock = wallclock
        self._lock = threading.Lock()
        self._live: dict[str, Claim] = {}
        self._restoring: dict[str, Claim] = {}
        # Claims that expired while some other method was looking, waiting for
        # pop_expired to hand them to whoever restores their baseline. Without
        # this, a claim noticed as expired by ``touching`` would sit restoring
        # forever with nobody told to restore it.
        self._unreported: list[Claim] = []

    # ------------------------- acquisition -------------------------

    def acquire(self, units: Iterable[str], *, holder: str, peer: str, ttl_s: float) -> Claim:
        """Claim every unit or none. Raises :class:`ClaimConflict`.

        Never waits: a run that cannot have everything is refused at once, so
        there is no acquisition order to get wrong and no deadlock.
        """
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        wanted = _minimal(units)
        if not wanted:
            raise ValueError("A claim needs at least one path")
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            for unit in wanted:
                for other in (*self._live.values(), *self._restoring.values()):
                    if other.overlaps(unit):
                        raise ClaimConflict(unit, other)
            claim = Claim(
                token=uuid.uuid4().hex,
                holder=holder,
                peer=peer,
                units=tuple(wanted),
                ttl_s=float(ttl_s),
                acquired_at=self._wallclock(),
                deadline=now + ttl_s,
            )
            self._live[claim.token] = claim
            return claim

    def renew(self, token: str) -> Claim:
        """Push a live claim's deadline out by its TTL. ``KeyError`` if not live."""
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            claim = self._live[token]
            claim.deadline = now + claim.ttl_s
            return claim

    def release(self, token: str) -> Claim | None:
        """Stop a live claim; its units stay unavailable until :meth:`restored`."""
        with self._lock:
            claim = self._live.pop(token, None)
            if claim is not None:
                claim.restoring = True
                self._restoring[token] = claim
            return claim

    def pop_expired(self) -> list[Claim]:
        """Claims that expired since the last call, each reported exactly once."""
        with self._lock:
            self._expire_locked(self._clock())
            reported, self._unreported = self._unreported, []
            return reported

    def restored(self, token: str) -> None:
        """Baseline restore finished; the units are free."""
        with self._lock:
            self._restoring.pop(token, None)

    def drop_all(self) -> list[Claim]:
        """Forget every claim without restoring — for server shutdown."""
        with self._lock:
            claims = [*self._live.values(), *self._restoring.values()]
            self._live.clear()
            self._restoring.clear()
            self._unreported.clear()
            return claims

    # ------------------------- queries -------------------------

    def live(self, token: str) -> Claim | None:
        with self._lock:
            self._expire_locked(self._clock())
            return self._live.get(token)

    def touching(self, path: str) -> list[Claim]:
        """Every claim — live or restoring — that overlaps ``path``."""
        with self._lock:
            self._expire_locked(self._clock())
            return [c for c in (*self._live.values(), *self._restoring.values()) if c.overlaps(path)]

    def snapshot(self) -> list[dict]:
        with self._lock:
            now = self._clock()
            self._expire_locked(now)
            claims = sorted(
                (*self._live.values(), *self._restoring.values()), key=lambda c: c.acquired_at
            )
            return [c.to_wire(now) for c in claims]

    # ------------------------- internals -------------------------

    def _expire_locked(self, now: float) -> None:
        expired = [c for c in self._live.values() if c.deadline <= now]
        for claim in expired:
            del self._live[claim.token]
            claim.restoring = True
            self._restoring[claim.token] = claim
            self._unreported.append(claim)


def _minimal(units: Iterable[str]) -> list[str]:
    """Distinct units with any unit already covered by another one dropped."""
    distinct = sorted(set(units), key=lambda u: (u.count("/"), u))
    kept: list[str] = []
    for unit in distinct:
        if not any(covers(k, unit) for k in kept):
            kept.append(unit)
    return kept
