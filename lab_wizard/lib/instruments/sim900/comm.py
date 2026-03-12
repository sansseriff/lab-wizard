from __future__ import annotations

from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.prologix_comm import (
    PrologixAddressedInstrumentDep,
)


class Sim900MainframeDep(Dependency):
    """Address-scoped transport for a SIM900 mainframe."""

    def __init__(self, gpib_comm: PrologixAddressedInstrumentDep):
        self.gpib_comm = gpib_comm

    def slot(self, slot: int, *, offline: bool = False) -> "Sim900SlotDep":
        return Sim900SlotDep(self.gpib_comm, slot, offline=offline)


class Sim900SlotDep(Dependency):
    """Slot-scoped transport for one SIM900 module."""

    def __init__(
        self,
        gpib_comm: PrologixAddressedInstrumentDep,
        slot: int,
        *,
        offline: bool = False,
    ):
        self.gpib_comm = gpib_comm
        self.slot = slot
        self.offline = offline

    def _wrap(self, cmd: str) -> str:
        return f'CONN {self.slot}, "esc"\r\n{cmd}\r\nesc'

    def write(self, cmd: str) -> int | bool:
        if self.offline:
            return True
        return self.gpib_comm.write(self._wrap(cmd))

    def read(self) -> bytes | str:
        if self.offline:
            return ""
        return self.gpib_comm.read()

    def query(self, cmd: str) -> bytes | str:
        if self.offline:
            return ""
        return self.gpib_comm.query(self._wrap(cmd))
