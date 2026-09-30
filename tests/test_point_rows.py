"""How readings become rows: the point rule in ``RunContext.observe``.

A row is everything recorded while the same parameter values were in force,
until a field would be recorded twice. These tests pin that rule down with the
cases it was designed around (``plans/semantic_data_plan.md`` §5): swapping
loop order, a reading taken at an outer level, repeats, retries, a reading
outside any loop, and every way a run can end.
"""

from __future__ import annotations

import json

import pytest

from lab_procedure import (
    MessageBus,
    Point,
    ProcedureRunner,
    Repeat,
    Retry,
    RunEnded,
    Sequence,
    Status,
    Step,
    StepBegan,
    Sweep,
    ValueAbove,
    Wait,
    WithParameter,
)

BIAS = [0.02, 0.03]
TRIGGER = [-50, -40, -30]


class Measure(Step):
    """Records one field, computed from the parameters in force."""

    def __init__(self, field: str, fn=lambda p: 0.0, name: str | None = None) -> None:
        super().__init__(name=name)
        self.field = field
        self.fn = fn

    def run(self) -> Status:
        assert self.context is not None
        self.context.observe({self.field: self.fn(self.context.parameters)})
        return Status.SUCCESS


class Probe(Step):
    """Notes how many rows had been emitted when it ran."""

    def __init__(self, seen: list[int], rows: list[Point]) -> None:
        super().__init__()
        self.seen = seen
        self.rows = rows

    def run(self) -> Status:
        self.seen.append(len(self.rows))
        return Status.SUCCESS


def rate(p: dict) -> float:
    return round(1e5 * (p["bias_voltage"] / 0.03) * (1 + p["trigger_mV"] / 100), 1)


def count() -> Measure:
    return Measure("count_rate", rate)


def volts() -> Measure:
    return Measure("device_voltage")


def run(tree: Step) -> tuple[list[Point], list[object]]:
    runner = ProcedureRunner()
    rows: list[Point] = []
    messages: list[object] = []
    runner.context.data_bus.subscribe(Point, rows.append)
    runner.context.data_bus.subscribe(object, messages.append)
    runner.run(tree)
    return rows, messages


def as_set(rows: list[Point]) -> set[str]:
    return {json.dumps(r.values, sort_keys=True) for r in rows}


# --------------------------- the rule ---------------------------


def test_readings_at_the_same_parameters_share_a_row():
    rows, _ = run(Sweep("bias_voltage", BIAS, lambda b: Sequence(count_at(b), volts())))
    assert [r.values for r in rows] == [
        {"bias_voltage": 0.02, "count_rate": 1.0, "device_voltage": 0.0},
        {"bias_voltage": 0.03, "count_rate": 1.0, "device_voltage": 0.0},
    ]
    assert [r.seq for r in rows] == [0, 1]


def count_at(_bias: object) -> Measure:
    return Measure("count_rate", lambda p: 1.0)


def test_swapping_loop_order_gives_the_same_rows_in_a_different_order():
    trigger_inner = Sweep(
        "bias_voltage", BIAS,
        lambda b: Sweep("trigger_mV", TRIGGER, lambda t: Sequence(count(), volts())),
    )
    bias_inner = Sweep(
        "trigger_mV", TRIGGER,
        lambda t: Sweep("bias_voltage", BIAS, lambda b: Sequence(count(), volts())),
    )
    a, _ = run(trigger_inner)
    b, _ = run(bias_inner)

    assert len(a) == len(b) == len(BIAS) * len(TRIGGER)
    assert as_set(a) == as_set(b)
    # The order is still recoverable: it is in seq, not in the values.
    assert [(r.values["bias_voltage"], r.values["trigger_mV"]) for r in a][:2] == [(0.02, -50), (0.02, -40)]
    assert [(r.values["bias_voltage"], r.values["trigger_mV"]) for r in b][:2] == [(0.02, -50), (0.03, -50)]


def test_a_reading_at_an_outer_level_gets_its_own_row_with_fewer_parameters():
    rows, _ = run(
        Sweep("bias_voltage", BIAS, lambda b: Sequence(volts(), Sweep("trigger_mV", TRIGGER, lambda t: count())))
    )
    assert len(rows) == len(BIAS) * (1 + len(TRIGGER))
    assert rows[0].values == {"bias_voltage": 0.02, "device_voltage": 0.0}
    assert rows[1].values == {"bias_voltage": 0.02, "trigger_mV": -50, "count_rate": 33333.3}


def test_recording_a_field_twice_at_the_same_parameters_is_refused():
    """Measure, wait, measure again, inside one sweep point: two rows nothing tells apart."""
    with pytest.raises(ValueError, match="'device_voltage' again at the same parameters"):
        run(Sweep("bias_voltage", [0.02], lambda b: Sequence(
            Measure("device_voltage", lambda p: 1.0), Wait(0), Measure("device_voltage", lambda p: 1.1),
        )))


def test_two_readings_of_one_quantity_are_one_row_under_two_names():
    rows, _ = run(Sweep("bias_voltage", [0.02], lambda b: Sequence(
        Measure("voltage_before", lambda p: 1.0), Wait(0), Measure("voltage_after", lambda p: 1.1),
    )))
    assert [r.values for r in rows] == [{"bias_voltage": 0.02, "voltage_before": 1.0, "voltage_after": 1.1}]


def test_or_a_row_each_when_labelled():
    rows, _ = run(Sweep("bias_voltage", [0.02], lambda b: Sequence(
        WithParameter("phase", "before", Measure("device_voltage", lambda p: 1.0)),
        Wait(0),
        WithParameter("phase", "after", Measure("device_voltage", lambda p: 1.1)),
    )))
    assert [r.values for r in rows] == [
        {"bias_voltage": 0.02, "phase": "before", "device_voltage": 1.0},
        {"bias_voltage": 0.02, "phase": "after", "device_voltage": 1.1},
    ]


def test_a_reading_outside_any_loop_is_its_own_row():
    # The mcr background count: labelled, but in no sweep.
    rows, _ = run(
        Sequence(
            WithParameter("phase", "background", Measure("counts", lambda p: 1)),
            WithParameter("phase", "signal", Sweep("attenuation_db", [20.0, 10.0], lambda a: Measure("counts", lambda p: 5))),
        )
    )
    assert [r.values for r in rows] == [
        {"phase": "background", "counts": 1},
        {"phase": "signal", "attenuation_db": 20.0, "counts": 5},
        {"phase": "signal", "attenuation_db": 10.0, "counts": 5},
    ]


def test_repeat_binds_its_index_so_repetitions_are_distinct_rows():
    rows, _ = run(Sweep("bias_voltage", [0.02], lambda b: Repeat(3, count_at(b))))
    assert [r.values for r in rows] == [
        {"bias_voltage": 0.02, "repeat": 0, "count_rate": 1.0},
        {"bias_voltage": 0.02, "repeat": 1, "count_rate": 1.0},
        {"bias_voltage": 0.02, "repeat": 2, "count_rate": 1.0},
    ]


def test_repeat_can_name_its_parameter():
    rows, _ = run(Repeat(2, Measure("counts"), parameter="shot"))
    assert [r.values["shot"] for r in rows] == [0, 1]


def test_a_retry_whose_failed_attempts_record_nothing_is_one_row():
    attempts = iter([Status.FAILED, Status.FAILED, Status.SUCCESS])

    class Flaky(Step):
        def run(self) -> Status:
            outcome = next(attempts)
            if outcome is Status.SUCCESS:
                assert self.context is not None
                self.context.observe({"counts": 7})
            return outcome

    rows, _ = run(Sweep("bias_voltage", [0.02], lambda b: Retry(3, Flaky())))
    assert [r.values for r in rows] == [{"bias_voltage": 0.02, "attempt": 2, "counts": 7}]


def test_a_retried_reading_keeps_every_attempt_as_its_own_row():
    """Measure, then check; the check fails once, so the reading is taken twice."""
    readings = iter([0.5, 2.0])
    rows, _ = run(Sweep("bias_voltage", [0.02], lambda b: Retry(3, Sequence(
        Measure("count_rate", lambda p: next(readings)), ValueAbove("count_rate", 1.0),
    ))))
    assert [r.values for r in rows] == [
        {"bias_voltage": 0.02, "attempt": 0, "count_rate": 0.5},
        {"bias_voltage": 0.02, "attempt": 1, "count_rate": 2.0},
    ]


def test_recording_a_bound_parameter_is_refused():
    with pytest.raises(ValueError, match="already a parameter in force"):
        run(Sweep("bias_voltage", [0.02], lambda b: Measure("bias_voltage")))


def test_conditions_read_a_value_before_its_row_closes():
    rows, _ = run(
        Sweep(
            "bias_voltage", [0.02],
            lambda b: Sequence(Measure("count_rate", lambda p: 5e6), ValueAbove("count_rate", 1e6), volts()),
        )
    )
    # ValueAbove succeeded, so the voltage was read and joined the same row.
    assert [r.values for r in rows] == [{"bias_voltage": 0.02, "count_rate": 5e6, "device_voltage": 0.0}]


# --------------------------- when rows appear ---------------------------


def test_a_row_is_emitted_when_its_loop_iteration_ends_not_one_point_late():
    rows: list[Point] = []
    seen: list[int] = []
    runner = ProcedureRunner()
    runner.context.data_bus.subscribe(Point, rows.append)
    runner.run(Sweep("bias_voltage", [1.0, 2.0, 3.0], lambda b: Sequence(Probe(seen, rows), count_at(b))))
    # At the start of iteration n, the rows of iterations 0..n-1 are out.
    assert seen == [0, 1, 2]
    assert len(rows) == 3


def test_every_row_is_emitted_before_run_ended():
    _, messages = run(Sequence(Measure("counts"), Measure("voltage")))
    kinds = [type(m).__name__ for m in messages]
    assert kinds == ["Point", "RunEnded"]
    assert messages[-1].status == "success"


def test_an_aborted_run_keeps_the_row_it_was_recording():
    runner = ProcedureRunner()
    messages: list[object] = []
    runner.context.data_bus.subscribe(object, messages.append)
    thread = runner.start(Sequence(Measure("counts"), Wait(10.0, progress_interval=0.01)))
    for _ in range(200):
        if thread.is_alive() and runner.context.latest:
            break
        thread.join(timeout=0.01)
    runner.abort()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert [type(m).__name__ for m in messages] == ["Point", "RunEnded"]
    assert messages[0].values == {"counts": 0.0}
    assert messages[1].status == "aborted"


def test_ctrl_c_is_recorded_as_aborted_and_keeps_the_open_row():
    class Interrupt(Step):
        def run(self) -> Status:
            raise KeyboardInterrupt

    runner = ProcedureRunner()
    messages: list[object] = []
    runner.context.data_bus.subscribe(object, messages.append)
    with pytest.raises(KeyboardInterrupt):
        runner.run(Sequence(Measure("counts"), Interrupt()))

    assert runner.status is Status.ABORTED
    assert [type(m).__name__ for m in messages] == ["Point", "RunEnded"]
    assert messages[-1].status == "aborted"


def test_a_failing_run_keeps_the_open_row_and_ends_failed():
    class Boom(Step):
        def run(self) -> Status:
            raise RuntimeError("counter timed out")

    runner = ProcedureRunner()
    messages: list[object] = []
    runner.context.data_bus.subscribe((Point, RunEnded), messages.append)
    with pytest.raises(RuntimeError):
        runner.run(Sequence(Measure("counts"), Boom()))

    assert [type(m).__name__ for m in messages] == ["Point", "RunEnded"]
    assert messages[-1].status == "failed"


# --------------------------- which steps a row came from ---------------------------


def test_a_row_names_the_steps_that_recorded_into_it():
    rows, _ = run(Sweep("bias_voltage", BIAS, lambda b: Sequence(count_at(b), volts()), name="bias"))
    # Iterations are "#n"; positions among a parent's children are "[n]".
    assert rows[1].steps == ("bias/sequence#1/measure[0]", "bias/sequence#1/measure[1]")


def test_repeat_and_retry_label_their_runs_as_iterations():
    status_bus = MessageBus()
    began: list[StepBegan] = []
    status_bus.subscribe(StepBegan, began.append)
    ProcedureRunner(status_bus=status_bus).run(Repeat(2, Retry(1, Measure("counts"))))
    paths = ["/".join(m.node_id) for m in began]
    assert paths == [
        "repeat",
        "repeat/retry#0",
        "repeat/retry#0/measure#0",
        "repeat/retry#1",
        "repeat/retry#1/measure#0",
    ]
    assert all(m.t.tzinfo is not None for m in began)


def test_a_row_is_stamped_with_its_last_reading():
    rows, messages = run(Sequence(Measure("counts"), Measure("voltage")))
    assert rows[0].t <= messages[-1].t
    assert rows[0].t.tzinfo is not None
