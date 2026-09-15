"""Own a local project's transports for the length of one run.

A project that opens instruments in its own process needs two different
guarantees, and neither check provides both:

* **No other process has the rack now, and none can take it mid-run.** A lease
  (:mod:`lab_wizard.lib.client.leases`) gives this. It also stops a server from
  opening the rack while we hold it — servers decline claimed transports.
* **No server opened the rack before we claimed it.** A lease cannot see that:
  a server holding a handle it opened earlier never wrote a lease file. The
  preflight check (:mod:`lab_wizard.lib.client.preflight`) asks every server on
  the machine what it holds.

So a run takes the leases first and then preflights. That order leaves no gap:
once the leases are held no server can open the rack, and preflight catches any
that already had. If either step fails, every lease taken so far is released.

Shared transports are skipped: something else already multiplexes them, so a
second process is the design rather than a conflict. See
:mod:`lab_wizard.lib.instruments.general.transport`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from lab_wizard.lib.client import leases
from lab_wizard.lib.client.preflight import preflight_local_project


logger = logging.getLogger(__name__)

__all__ = ["LocalTransportClaim", "exclusive_transport_keys"]


def exclusive_transport_keys(instruments: Mapping[str, Any]) -> list[str]:
    """Distinct transport keys of a project's roots that cannot be shared.

    A root with no transport key cannot be leased — preflight still covers it by
    root path. Two roots naming one device yield one key, claimed once.
    """
    keys: set[str] = set()
    for params in instruments.values():
        sharing = getattr(params, "transport_sharing", None)
        try:
            if callable(sharing) and sharing() == "shared":
                continue
            key_of = getattr(params, "transport_key", None)
            key = key_of() if callable(key_of) else None
        except Exception:  # noqa: BLE001 - a broken declaration must not block a run
            logger.warning("Could not read transport declarations from %r", params, exc_info=True)
            continue
        if key:
            keys.add(key)
    return sorted(keys)


class LocalTransportClaim:
    """Context manager: lease every exclusive transport, then preflight.

        with LocalTransportClaim(project.resources.instruments, owner="pcr_run_1"):
            ...  # construct and use the instruments
    """

    def __init__(
        self,
        instruments: Mapping[str, Any],
        *,
        owner: str,
        note: str = "",
        check_servers: bool = True,
    ) -> None:
        self.instruments = dict(instruments)
        self.owner = owner
        self.note = note
        self.check_servers = check_servers
        self.held: list[str] = []

    def __enter__(self) -> "LocalTransportClaim":
        try:
            for key in exclusive_transport_keys(self.instruments):
                leases.acquire(key, owner=self.owner, note=self.note)
                self.held.append(key)
            if self.check_servers and self.instruments:
                preflight_local_project(self.instruments)
        except BaseException:
            self._release()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self._release()

    def _release(self) -> None:
        for key in reversed(self.held):
            leases.release(key)
        self.held = []
