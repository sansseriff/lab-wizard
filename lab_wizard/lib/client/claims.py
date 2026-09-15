"""Claim the instruments a run drives through a server.

The client half of ``plans/server_plan.md`` Phase 9. For the length of a run it:

1. asks each server for a claim on the instruments routed to it — all of them,
   or none, across every server;
2. puts the token on the session those instruments' proxies call through, so
   every write carries it;
3. renews each claim on a background thread, over a **separate session**. The
   proxies' session serializes its calls, so a renewal sent on it would wait
   behind a long ``count()`` — long enough for the claim to expire mid-run;
4. releases the claims and clears the tokens on exit.

A renewal that fails means the claim is gone. Nothing here can interrupt the
procedure, so it is logged; the server refuses the run's next write with a
message saying the claim is no longer held, which stops the run.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from contextlib import ExitStack
from typing import Any, Optional

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.client.session import Session


logger = logging.getLogger(__name__)

__all__ = ["RemoteClaim", "RoutedClaims"]

DEFAULT_TTL_S = 30.0


class RemoteClaim:
    """One claim on one server, renewed while held."""

    def __init__(
        self,
        url: str,
        paths: Iterable[str],
        *,
        holder: str,
        ttl_s: float = DEFAULT_TTL_S,
        timeout_ms: int = 5_000,
    ) -> None:
        self.url = url
        self.paths = sorted(set(paths))
        self.holder = holder
        self.ttl_s = ttl_s
        self.timeout_ms = timeout_ms
        self.token: Optional[str] = None
        self.units: list[str] = []
        self.lost = threading.Event()
        self._control: Optional[Session] = None
        self._stop = threading.Event()
        self._renewer: Optional[threading.Thread] = None

    def __enter__(self) -> "RemoteClaim":
        self._control = Session(self.url, timeout_ms=self.timeout_ms, client_name=f"claims:{self.holder}")
        try:
            result = self._control.call(
                "claim_acquire", {"paths": self.paths, "holder": self.holder, "ttl_s": self.ttl_s}
            )
        except BaseException:
            self._control.close()
            self._control = None
            raise
        self.token = result["token"]
        self.units = list(result.get("units") or [])
        self._renewer = threading.Thread(
            target=self._renew_until_stopped, name=f"claim-renew:{self.holder}", daemon=True
        )
        self._renewer.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        if self._renewer is not None:
            self._renewer.join(timeout=self.timeout_ms / 1000 + 1)
        control, self._control = self._control, None
        if control is None:
            return
        try:
            if self.token is not None:
                control.call("claim_release", {"token": self.token})
        except Exception as exc:  # noqa: BLE001 - the server expires it anyway
            logger.warning("Could not release claim on %s (%s); it will expire", self.url, exc)
        finally:
            control.close()

    def _renew_until_stopped(self) -> None:
        interval = max(self.ttl_s / 3.0, 0.05)
        while not self._stop.wait(interval):
            control = self._control
            if control is None:
                return
            try:
                control.call("claim_renew", {"token": self.token})
            except Exception as exc:  # noqa: BLE001 - reported, and the server enforces it
                self.lost.set()
                logger.error(
                    "Lost the claim on %s for %s (%s). The server will refuse this "
                    "run's next write.",
                    self.url,
                    ", ".join(self.units),
                    exc,
                )
                return


class RoutedClaims:
    """Claim every server-routed instrument among ``instruments``.

    Instruments that are not remote proxies are ignored — local ones are
    claimed by ``LocalTransportClaim`` before they are constructed. Proxies are
    grouped by the session they call through, one claim per server.
    """

    def __init__(self, instruments: Iterable[Any], *, holder: str, ttl_s: float = DEFAULT_TTL_S) -> None:
        self.holder = holder
        self.ttl_s = ttl_s
        self._by_session: dict[int, tuple[Session, list[str]]] = {}
        for instrument in instruments:
            if not isinstance(instrument, RemoteProxy):
                continue
            session = instrument._session
            entry = self._by_session.setdefault(id(session), (session, []))
            entry[1].append(instrument._inst_path)
        self.claims: list[RemoteClaim] = []
        self._stack: Optional[ExitStack] = None

    def __enter__(self) -> "RoutedClaims":
        stack = ExitStack()
        try:
            for session, paths in self._by_session.values():
                claim = stack.enter_context(
                    RemoteClaim(session.url, paths, holder=self.holder, ttl_s=self.ttl_s)
                )
                session.claim_token = claim.token
                stack.callback(setattr, session, "claim_token", None)
                self.claims.append(claim)
        except BaseException:
            stack.close()
            raise
        self._stack = stack
        return self

    def __exit__(self, *_exc: object) -> None:
        stack, self._stack = self._stack, None
        if stack is not None:
            stack.close()
