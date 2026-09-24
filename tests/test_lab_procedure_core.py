from __future__ import annotations

import time

from lab_procedure import (
    MessageBus,
    Point,
    ProcedureRunner,
    RunStarted,
    Sequence,
    Status,
    Step,
    Sweep,
    Wait,
)


class Record(Step):
    def __init__(self, value: object, name: str | None = None) -> None:
        super().__init__(name=name)
        self.value = value

    def run(self) -> Status:
        assert self.context is not None
        self.context.observe({"value": self.value})
        return Status.SUCCESS


def test_a_sweep_records_flat_rows_carrying_the_swept_parameter() -> None:
    data_bus = MessageBus()
    status_bus = MessageBus()
    data_messages: list[object] = []
    status_messages: list[object] = []
    data_bus.subscribe(object, data_messages.append)
    status_bus.subscribe(object, status_messages.append)

    procedure = Sequence(
        Sweep("bias_voltage", [0.0, 0.1, 0.2], lambda value: Record(value)),
        name="root",
    )

    runner = ProcedureRunner(data_bus=data_bus, status_bus=status_bus)
    status = runner.run(procedure, RunStarted(run_type="unit_test"))

    assert status is Status.SUCCESS
    points = [m for m in data_messages if isinstance(m, Point)]
    assert [p.values for p in points] == [
        {"bias_voltage": 0.0, "value": 0.0},
        {"bias_voltage": 0.1, "value": 0.1},
        {"bias_voltage": 0.2, "value": 0.2},
    ]
    assert [p.seq for p in points] == [0, 1, 2]
    assert status_messages


def test_wait_can_be_aborted_from_runner_thread() -> None:
    runner = ProcedureRunner()
    thread = runner.start(Wait(10.0, progress_interval=0.01))

    runner.abort()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert runner.status is Status.ABORTED


def test_abort_reaches_dynamic_sweep_child() -> None:
    runner = ProcedureRunner()
    procedure = Sweep("bias_voltage", [0.1], lambda _: Wait(10.0, progress_interval=0.01))
    thread = runner.start(procedure)

    time.sleep(0.05)
    runner.abort()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert runner.status is Status.ABORTED
