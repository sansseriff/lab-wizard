from __future__ import annotations

from lab_procedure.messages import Point, RunEnded, RunStarted

from lab_wizard.lib.savers.saver import GenericSaver


class SaverSink:
    """Bridge lab_procedure messages to lab_wizard saver instances."""

    def __init__(self, savers: list[GenericSaver]) -> None:
        self.savers = list(savers)

    def handle(self, message: RunStarted | Point | RunEnded) -> None:
        if isinstance(message, RunStarted):
            for saver in self.savers:
                saver.start_run(
                    run_type=message.run_type,
                    device=message.device,
                    cryostat=message.cryostat,
                    operator=message.operator,
                    description=message.description,
                    config=message.config,
                    instruments=message.instruments,
                )
            return

        if isinstance(message, Point):
            values = message.values
            counts = values.get("counts")
            int_time = values.get("int_time")
            delta_time = values.get("delta_time")
            temperature = values.get("temperature")
            metadata = {"seq": message.seq, "steps": list(message.steps)}
            for saver in self.savers:
                saver.write_measurement(
                    data=values,
                    counts=int(counts) if counts is not None else None,
                    int_time=float(int_time) if int_time is not None else None,
                    delta_time=float(delta_time) if delta_time is not None else None,
                    temperature=float(temperature) if temperature is not None else None,
                    metadata=metadata,
                    details=[],
                )
            return

        if isinstance(message, RunEnded):
            for saver in self.savers:
                saver.end_run()
