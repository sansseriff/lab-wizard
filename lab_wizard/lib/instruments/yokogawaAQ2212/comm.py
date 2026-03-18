from __future__ import annotations

from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.visa import LocalVisaDep


class YokoAQ2212Dep(Dependency):
    """Frame-level VISA TCP comm for Yokogawa AQ2212."""

    def __init__(self, ip_address: str, ip_port: int, *, offline: bool = False):
        self.offline = offline
        resource = f"TCPIP0::{ip_address}::{ip_port}::SOCKET"
        self._visa = LocalVisaDep(resource)  # lazy — connects on first use

    def write(self, cmd: str) -> None:
        if self.offline:
            return
        self._visa.write(cmd)

    def query(self, cmd: str) -> str:
        if self.offline:
            return ""
        return self._visa.query(cmd)

    def slot(self, slot: int) -> "YokoAQ2212SlotDep":
        return YokoAQ2212SlotDep(self, slot)


class YokoAQ2212SlotDep(Dependency):
    """Slot-scoped dep: passes SCPI commands through with offline gating.

    AQ2212 SCPI commands already embed the slot number inline
    (e.g. ``SOUR1:FREQ?``, ``INP2:ATT?``), so this dep just gates
    offline mode and provides a clean interface for module classes.
    """

    def __init__(self, frame: YokoAQ2212Dep, slot: int):
        self._frame = frame
        self.slot = slot
        self.offline = frame.offline

    def write(self, cmd: str) -> None:
        self._frame.write(cmd)

    def query(self, cmd: str) -> str:
        return self._frame.query(cmd)
