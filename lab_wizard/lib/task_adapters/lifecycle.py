"""What happens around a procedure when it runs.

Every run does the same seven things, and the order is the point, so it is written
once here instead of in every generated setup file:

1. **claim**    the transports this process will open (``claims``)
2. **resolve**  the instruments — *after* claiming, because constructing a local
                rack can open its serial port, and opening before claiming is the
                race claims exist to close
3. **claim**    the instruments reached through a server (``claims_after_resolve``).
                Resolving a remote proxy opens nothing, and only the resolved
                proxies say which server and path each instrument is
4. **baseline** ``apply_baseline()`` on every bound instrument, so a setting an
                earlier experiment changed cannot carry into this one
5. **run**      the measurement
6. **safe**     ``enter_safe_state()`` on every bound instrument that declares one,
                **if the run did not succeed** — while every claim is still held
7. **release**  the claims, always

Step 6 runs only on failure, abort, or an exception. A run that completes has
already been through its own guards (``SafeGuard``, ``SourceGuard``) and ended
where its procedure chose to — including states a measurement deliberately
exposes, like an IV curve's ``turn_off_at_end=False``. Forcing a safe state
after a successful run would override that choice; the lifecycle is the
backstop for runs that never reached their own cleanup.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager, ExitStack
from typing import Any, TypeVar

from lab_procedure import Status

from lab_wizard.lib.instruments.general.behavior import InstrumentBehavior


logger = logging.getLogger(__name__)

__all__ = ["RunLifecycle", "bound_instruments"]

R = TypeVar("R")


def bound_instruments(resources: object) -> list[InstrumentBehavior]:
    """Instruments bound to a resources object's fields, in field order.

    Fields holding a behavior, or a list of them, count; params do not. One instrument bound to two roles appears once. A remote
    proxy is a behavior too, so baseline and safe state reach the server's
    instrument. A ``RemoteOpaque`` is not — nothing is known about what it
    does, so nothing is done to it.
    """
    if dataclasses.is_dataclass(resources) and not isinstance(resources, type):
        values: Iterable[Any] = (getattr(resources, f.name) for f in dataclasses.fields(resources))
    else:
        values = vars(resources).values()

    found: dict[int, InstrumentBehavior] = {}
    for value in values:
        items = value if isinstance(value, (list, tuple)) else (value,)
        for item in items:
            if isinstance(item, InstrumentBehavior):
                found.setdefault(id(item), item)
    return list(found.values())


class RunLifecycle:
    """Claim, resolve, claim through servers, baseline, run, make safe on failure, release."""

    def __init__(
        self,
        *,
        claims: Sequence[AbstractContextManager[Any]] = (),
        claims_after_resolve: Sequence[
            Callable[[list[InstrumentBehavior]], AbstractContextManager[Any]]
        ] = (),
    ) -> None:
        self.claims = list(claims)
        self.claims_after_resolve = list(claims_after_resolve)

    def run(self, resolve: Callable[[], R], execute: Callable[[R], Status]) -> Status:
        """Run one measurement. ``resolve`` builds the resources; ``execute`` runs them."""
        with ExitStack() as held:
            # All or nothing: if a later claim fails, ExitStack releases the
            # earlier ones on the way out.
            for claim in self.claims:
                held.enter_context(claim)

            resources = resolve()
            instruments = bound_instruments(resources)
            for make_claim in self.claims_after_resolve:
                held.enter_context(make_claim(instruments))
            self._apply_baseline(instruments)

            try:
                status = execute(resources)
            except BaseException:
                # Includes KeyboardInterrupt: a Ctrl-C mid-sweep is exactly the
                # run that never reached its own guards.
                self._enter_safe_state(instruments, "raised", exception_in_flight=True)
                raise
            if status is not Status.SUCCESS:
                self._enter_safe_state(instruments, status.value, exception_in_flight=False)
            return status

    @staticmethod
    def _apply_baseline(instruments: list[InstrumentBehavior]) -> None:
        for instrument in instruments:
            if instrument.apply_baseline() is False:
                raise RuntimeError(
                    f"{type(instrument).__name__} could not apply its configured "
                    "baseline; not starting the run"
                )

    @staticmethod
    def _enter_safe_state(
        instruments: list[InstrumentBehavior], outcome: str, *, exception_in_flight: bool
    ) -> None:
        """Attempt every instrument's safe state; never mask the run's own error.

        After an exception the failures are logged, since raising would replace
        the exception already propagating. After a failed or aborted status —
        no exception in flight — a safe state that did not take is raised: it
        leaves hardware biased, and it must not pass as a quiet non-zero exit.
        """
        failures: list[str] = []
        for instrument in instruments:
            # Looked up on the class: a proxy answers any attribute reflectively.
            if not callable(getattr(type(instrument), "enter_safe_state", None)):
                continue
            label = getattr(instrument, "attribute_name", None) or type(instrument).__name__
            try:
                if getattr(instrument, "enter_safe_state")() is False:
                    failures.append(f"{label}: reported failure")
            except Exception as exc:  # noqa: BLE001 - every instrument is attempted
                failures.append(f"{label}: {exc!r}")
        if not failures:
            return
        detail = "; ".join(failures)
        logger.error("Run %s and the safe state did not take: %s", outcome, detail)
        if not exception_in_flight:
            raise RuntimeError(f"Run {outcome}; safe state failed: {detail}")
