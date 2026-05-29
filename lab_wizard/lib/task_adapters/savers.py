from __future__ import annotations

from lab_procedure.messages import Observation, RunEnded, RunStarted

from lab_wizard.lib.savers.saver import GenericSaver


class SaverSink:
    """Bridge lab_procedure messages to lab_wizard saver instances."""

    def __init__(self, savers: list[GenericSaver]) -> None:
        self.savers = list(savers)

    def handle(self, message: RunStarted | Observation | RunEnded) -> None:
        if isinstance(message, RunStarted):
            for saver in self.savers:
                saver.start_run(
                    run_type=message.run_type,
                    device=message.device,
                    cryostat=message.cryostat,
                    operator=message.operator,
                    description=message.description,
                    config=message.config,
                )
            return

        if isinstance(message, Observation):
            counts = message.data.get("counts")
            int_time = message.data.get("int_time")
            delta_time = message.data.get("delta_time")
            metadata = dict(message.metadata)
            if message.sequence_index is not None:
                metadata["sequence_index"] = message.sequence_index
            if message.sweep_index is not None:
                metadata["sweep_index"] = message.sweep_index
            for saver in self.savers:
                saver.write_measurement(
                    data=message.data,
                    counts=int(counts) if counts is not None else None,
                    int_time=float(int_time) if int_time is not None else None,
                    delta_time=float(delta_time) if delta_time is not None else None,
                    temperature=message.temperature,
                    metadata=metadata,
                    details=message.details,
                )
            return

        if isinstance(message, RunEnded):
            for saver in self.savers:
                saver.end_run()
