from __future__ import annotations

from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep


class AndoAQ8201AFrameDep(Dependency):
    """Frame-level dep for the Ando AQ8201A mainframe.

    Wraps a ``PrologixAddressedInstrumentDep`` and exposes frame-level
    commands plus a ``.slot()`` factory for slot-scoped deps.
    """

    def __init__(self, gpib_comm: PrologixAddressedInstrumentDep):
        self.gpib_comm = gpib_comm

    def slot(self, slot: int, *, offline: bool = False) -> "AndoAQ8201ASlotDep":
        return AndoAQ8201ASlotDep(self.gpib_comm, slot, offline=offline)


class AndoAQ8201ASlotDep(Dependency):
    """Slot-scoped comm: prefixes all commands with C{slot}\\n as Ando protocol requires."""

    def __init__(self, gpib_comm: PrologixAddressedInstrumentDep, slot: int, *, offline: bool = False):
        self._gpib = gpib_comm
        self.slot = slot
        self.offline = offline

    def write(self, cmd: str) -> None:
        if self.offline:
            return
        self._gpib.write(f"C{self.slot}\n{cmd}")

    def query(self, cmd: str) -> str:
        if self.offline:
            return ""
        self._gpib.write(f"C{self.slot}\n{cmd}")
        raw = self._gpib.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace").strip()
        return str(raw).strip()
