from __future__ import annotations

import time

from lab_procedure import (
    MessageBus,
    Observation,
    ProcedureRunner,
    RunStarted,
    Sequence,
    Status,
    Step,
    Sweep,
    Wait,
)


class EmitObservation(Step):
    def __init__(self, value: object, name: str | None = None) -> None:
        super().__init__(name=name)
        self.value = value

    def run(self) -> Status:
        assert self.context is not None
        self.context.data_bus.emit(
            Observation(
                data={"value": self.value},
                metadata=self.context.snapshot_parameters(),
                sequence_index=self.context.next_sequence_index(),
                sweep_index=self.context.sweep_index,
            )
        )
        return Status.SUCCESS


def test_sequence_sweep_emits_flat_observations_with_parameter_snapshots() -> None:
    data_bus = MessageBus()
    status_bus = MessageBus()
    data_messages: list[object] = []
    status_messages: list[object] = []
    data_bus.subscribe(object, data_messages.append)
    status_bus.subscribe(object, status_messages.append)

    procedure = Sequence(
        Sweep("bias_voltage", [0.0, 0.1, 0.2], lambda value: EmitObservation(value)),
        name="root",
    )

    runner = ProcedureRunner(data_bus=data_bus, status_bus=status_bus)
    status = runner.run(procedure, RunStarted(run_type="unit_test"))

    assert status is Status.SUCCESS
    observations = [m for m in data_messages if isinstance(m, Observation)]
    assert [m.data["value"] for m in observations] == [0.0, 0.1, 0.2]
    assert [m.metadata["bias_voltage"] for m in observations] == [0.0, 0.1, 0.2]
    assert [m.sequence_index for m in observations] == [0, 1, 2]
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
