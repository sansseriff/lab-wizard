"""Behavior-tree control flow: Retry, If, Selector, Invert, and conditions.

Branching in a procedure is composition of steps that return SUCCESS, FAILED
or ABORTED, so these tests are mostly truth tables. ABORTED must always pass
straight through — an operator's stop is never a failure to work around.
"""

from __future__ import annotations

import pytest

from lab_procedure import (
    If,
    Invert,
    ProcedureRunner,
    Retry,
    Selector,
    Sequence,
    Status,
    Step,
    ValueAbove,
    ValueBelow,
)
from lab_procedure.messages import Observation

from lab_wizard.lib.instruments.general.counter import StandInCounter
from lab_wizard.lib.task_adapters.instrument_steps import Count


class Script(Step):
    """Returns (or raises) the next outcome each time it runs."""

    def __init__(self, *outcomes: Status | Exception, name: str = "script") -> None:
        super().__init__(name=name)
        self.outcomes = list(outcomes)
        self.runs = 0

    def run(self) -> Status:
        outcome = self.outcomes[min(self.runs, len(self.outcomes) - 1)]
        self.runs += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _run(step: Step) -> Status:
    return ProcedureRunner().run(step)


S, F, A = Status.SUCCESS, Status.FAILED, Status.ABORTED


# --------------------------- Retry ---------------------------


def test_retry_stops_at_the_first_success():
    child = Script(F, F, S, F)
    assert _run(Retry(5, child)) is S
    assert child.runs == 3


def test_retry_retries_a_raised_error_and_re_raises_the_last_one():
    flaky = Script(TimeoutError("gate 1"), S)
    assert _run(Retry(2, flaky)) is S

    broken = Script(TimeoutError("gate"), TimeoutError("still"))
    with pytest.raises(TimeoutError, match="still"):
        _run(Retry(2, broken))


def test_retry_gives_up_failed_and_never_retries_an_abort():
    assert _run(Retry(3, Script(F))) is F
    aborted = Script(A, S)
    assert _run(Retry(3, aborted)) is A
    assert aborted.runs == 1


# --------------------------- If / Selector / Invert ---------------------------


@pytest.mark.parametrize(
    ("verdict", "otherwise", "expected", "ran"),
    [
        (S, True, F, "then"),
        (F, True, S, "otherwise"),
        (F, False, S, None),  # no otherwise: skipped, and that is success
        (A, True, A, None),
    ],
)
def test_if_runs_exactly_the_chosen_branch(verdict, otherwise, expected, ran):
    then, other = Script(F, name="then"), Script(S, name="otherwise")
    step = If(Script(verdict), then, other if otherwise else None)
    assert _run(step) is expected
    assert {"then": then.runs, "otherwise": other.runs} == {
        "then": int(ran == "then"),
        "otherwise": int(ran == "otherwise"),
    }


def test_selector_falls_back_until_something_succeeds():
    first, second, third = Script(F), Script(S), Script(S)
    assert _run(Selector(first, second, third)) is S
    assert (first.runs, second.runs, third.runs) == (1, 1, 0)
    assert _run(Selector(Script(F), Script(F))) is F
    assert _run(Selector(Script(A), Script(S))) is A


@pytest.mark.parametrize(("child", "expected"), [(S, F), (F, S), (A, A)])
def test_invert(child, expected):
    assert _run(Invert(Script(child))) is expected


# --------------------------- conditions on recorded values ---------------------------


class Counts(StandInCounter):
    def __init__(self, *values: int) -> None:
        super().__init__()
        self.values = list(values)

    def count(self, gate_time: float | None = None) -> int:
        return self.values.pop(0)


def test_conditions_read_the_latest_recorded_value():
    counter = Counts(50, 5_000)
    low = Sequence(Count(counter, 1.0), ValueBelow("count_rate", 1_000.0))
    assert _run(low) is S
    high = Sequence(Count(counter, 1.0), ValueAbove("count_rate", 1_000.0))
    assert _run(high) is S


def test_a_failed_condition_stops_a_sequence_which_is_how_abort_if_works():
    counter = Counts(5_000, 1)
    after = Script(S, name="after")
    assert _run(Sequence(Count(counter, 1.0), ValueBelow("count_rate", 1_000.0), after)) is F
    assert after.runs == 0


def test_a_condition_on_a_value_never_recorded_is_an_error_not_a_failure():
    with pytest.raises(KeyError, match="recorded no such value"):
        _run(ValueAbove("count_rate", 1.0))


def test_observations_are_flat_rows_carrying_the_swept_parameters():
    from lab_procedure import Sweep

    runner = ProcedureRunner()
    rows: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, rows.append)
    counter = Counts(10, 20)
    runner.run(Sweep("bias_voltage", [0.1, 0.2], lambda v: Count(counter, 2.0)))

    assert [r.data for r in rows] == [
        {"bias_voltage": 0.1, "counts": 10, "int_time": 2.0, "count_rate": 5.0},
        {"bias_voltage": 0.2, "counts": 20, "int_time": 2.0, "count_rate": 10.0},
    ]
    assert rows[0].metadata == {"bias_voltage": 0.1}
