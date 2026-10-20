"""RunLifecycle: claim, resolve, baseline, run, safe on failure, release.

The ordering is the whole contract, so most of these tests record one shared
event list and assert on it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import cast

import pytest

from lab_procedure import Status

from lab_wizard.lib.client.proxies.base import RemoteOpaque
from lab_wizard.lib.client.session import Session
from lab_wizard.lib.instruments.general.counter import StandInCounter
from lab_wizard.lib.instruments.general.vsource import StandInVSource
from lab_wizard.lib.task_adapters.sinks import StandInSink
from lab_wizard.lib.task_adapters.lifecycle import RunLifecycle, bound_instruments


class Claim:
    def __init__(self, events: list[str], label: str, *, fail: bool = False) -> None:
        self.events, self.label, self.fail = events, label, fail

    def __enter__(self) -> "Claim":
        if self.fail:
            self.events.append(f"refuse {self.label}")
            raise RuntimeError(f"{self.label} is taken")
        self.events.append(f"claim {self.label}")
        return self

    def __exit__(self, *_exc: object) -> None:
        self.events.append(f"release {self.label}")


class Source(StandInVSource):
    def __init__(self, events: list[str], *, baseline_ok: bool = True, safe_ok: bool = True) -> None:
        super().__init__()
        self.events, self.baseline_ok, self.safe_ok = events, baseline_ok, safe_ok

    def apply_baseline(self) -> bool:
        self.events.append("baseline source")
        return self.baseline_ok

    def enter_safe_state(self) -> bool:
        self.events.append("safe source")
        return self.safe_ok


class Counter(StandInCounter):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self.events = events

    def apply_baseline(self) -> bool:
        self.events.append("baseline counter")
        return True


@dataclass
class Resources:
    source: Source
    counter: Counter
    savers: list = field(default_factory=list)


def _lifecycle(events: list[str], *, claims=None, outcome=Status.SUCCESS, source=None):
    source = source or Source(events)

    def resolve() -> Resources:
        events.append("resolve")
        return Resources(source=source, counter=Counter(events), savers=[StandInSink()])

    def execute(_resources: Resources) -> Status:
        events.append("execute")
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    lifecycle = RunLifecycle(claims=claims if claims is not None else [Claim(events, "rack")])
    return lifecycle, resolve, execute


def test_a_successful_run_claims_first_resolves_second_and_is_not_made_safe():
    """Safe state is the backstop for runs that never reached their own guards.
    A completed run already did, and may have ended where it chose to."""
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(events)

    assert lifecycle.run(resolve, execute) is Status.SUCCESS
    assert events == [
        "claim rack",
        "resolve",
        "baseline source",
        "baseline counter",
        "execute",
        "release rack",
    ]


@pytest.mark.parametrize("outcome", [Status.FAILED, Status.ABORTED])
def test_a_run_that_does_not_succeed_is_made_safe_before_release(outcome):
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(events, outcome=outcome)

    assert lifecycle.run(resolve, execute) is outcome
    # Only the source declares a safe state; the counter is left alone.
    assert events[-3:] == ["execute", "safe source", "release rack"]


@pytest.mark.parametrize("error", [RuntimeError("driver fault"), KeyboardInterrupt()])
def test_a_run_that_raises_is_made_safe_and_the_error_propagates(error):
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(events, outcome=error)

    with pytest.raises(type(error)):
        lifecycle.run(resolve, execute)
    assert events[-3:] == ["execute", "safe source", "release rack"]


def test_a_failed_safe_state_does_not_mask_the_runs_own_exception(caplog):
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(
        events, outcome=RuntimeError("the real fault"), source=Source(events, safe_ok=False)
    )

    with caplog.at_level(logging.ERROR), pytest.raises(RuntimeError, match="the real fault"):
        lifecycle.run(resolve, execute)
    assert "safe state did not take" in caplog.text
    assert events[-1] == "release rack"


def test_a_failed_safe_state_after_a_failed_status_is_raised():
    """No exception is in flight, so nothing would be masked — and a source left
    biased must not pass as a quiet non-zero exit."""
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(
        events, outcome=Status.FAILED, source=Source(events, safe_ok=False)
    )

    with pytest.raises(RuntimeError, match="safe state failed"):
        lifecycle.run(resolve, execute)
    assert events[-1] == "release rack"


def test_a_refused_baseline_stops_the_run_before_it_starts():
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(events, source=Source(events, baseline_ok=False))

    with pytest.raises(RuntimeError, match="could not apply its configured baseline"):
        lifecycle.run(resolve, execute)
    assert "execute" not in events
    assert "safe source" not in events
    assert events[-1] == "release rack"


def test_claims_are_all_or_nothing():
    events: list[str] = []
    lifecycle, resolve, execute = _lifecycle(
        events, claims=[Claim(events, "rack"), Claim(events, "counter", fail=True)]
    )

    with pytest.raises(RuntimeError, match="counter is taken"):
        lifecycle.run(resolve, execute)
    assert events == ["claim rack", "refuse counter", "release rack"]


# --------------------------- which instruments ---------------------------


def test_bound_instruments_takes_behaviors_from_fields_and_lists_once_each():
    events: list[str] = []
    source = Source(events)

    @dataclass
    class Twice:
        bias: Source
        also_bias: Source
        detectors: list
        savers: list
        params: dict

    counters = [Counter(events), Counter(events)]
    found = bound_instruments(
        Twice(bias=source, also_bias=source, detectors=counters, savers=[StandInSink()], params={})
    )
    assert found == [source, *counters]


def test_an_opaque_proxy_is_not_touched():
    """Nothing is known about what it does, so neither baseline nor safe state
    is sent to it."""

    @dataclass
    class WithOpaque:
        mystery: object

    opaque = RemoteOpaque(cast(Session, object()), "inst://x")
    assert bound_instruments(WithOpaque(mystery=opaque)) == []
