from __future__ import annotations

import time

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

    def query_port(self, cmd: str, *, timeout_s: float = 2.0) -> bytes | str:
        """Query the module through the mainframe's buffered port, waiting for the reply.

        ``query`` reads whatever arrives within the Prologix read timeout. A
        module slower than that (a SIM970 takes ~270 ms per reading) answers
        after the read has given up, its reply waits in the mainframe, and the
        next query returns it instead of its own: every reading one behind.
        Here the port is flushed, the command sent with ``SNDT``, and the
        mainframe polled (``NINP?``/``GETN?``) until a whole line has come back.
        Returns ``b""`` if none arrives within ``timeout_s``.
        """
        if self.offline:
            return ""
        self.gpib_comm.write(f"FLSH {self.slot}")
        self.gpib_comm.write(f'SNDT {self.slot},"{cmd}"')
        reply = b""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            waiting = int(self.gpib_comm.query(f"NINP? {self.slot}").strip() or 0)
            if waiting:
                reply += self.gpib_comm.query_block(f"GETN? {self.slot},{waiting}")
                if reply.endswith(b"\n"):
                    return reply
            else:
                time.sleep(0.01)
        return reply if reply.endswith(b"\n") else b""
