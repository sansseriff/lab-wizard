from __future__ import annotations

from threading import Thread

from lab_procedure.bus import MessageBus
from lab_procedure.context import RunContext
from lab_procedure.core import Status, Step
from lab_procedure.messages import RunEnded, RunStarted, StepFailed


class ProcedureRunner:
    """Owns execution of a Step tree and emits run lifecycle messages."""

    def __init__(
        self,
        *,
        context: RunContext | None = None,
        data_bus: MessageBus | None = None,
        status_bus: MessageBus | None = None,
        instruments: object | None = None,
    ) -> None:
        self.context = context or RunContext(
            data_bus=data_bus or MessageBus(),
            status_bus=status_bus or MessageBus(),
            instruments=instruments,
        )
        self._thread: Thread | None = None
        self._root: Step | None = None
        self.status: Status | None = None

    def run(self, root: Step, run_started: RunStarted | None = None) -> Status:
        self._root = root
        if run_started is not None:
            self.context.data_bus.emit(run_started)
        try:
            self.status = root.execute(self.context)
            return self.status
        except Exception as exc:
            self.status = Status.FAILED
            node_id = root.node_id or (root.name,)
            self.context.data_bus.emit(StepFailed(node_id, str(exc)))
            raise
        finally:
            self.context.data_bus.emit(RunEnded((self.status or Status.FAILED).value))

    def start(self, root: Step, run_started: RunStarted | None = None) -> Thread:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("ProcedureRunner is already running")
        self._thread = Thread(target=self.run, args=(root, run_started), daemon=True)
        self._thread.start()
        return self._thread

    def abort(self) -> None:
        if self._root is not None:
            self._root.abort()
