from __future__ import annotations

import time

from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.serial import SerialDep


class PrologixControllerDep(Dependency):
    """Serial-backed Prologix controller transport.

    This object owns the physical serial connection to the Prologix controller
    and exposes helpers for controller commands and instrument-scoped GPIB I/O.
    """

    def __init__(self, serial_dep: SerialDep, *, read_delay_s: float = 0.1):
        self.serial_dep = serial_dep
        self.read_delay_s = read_delay_s

    def write_controller(self, command: str) -> int:
        return self.serial_dep.write(f"{command}\n")

    def read_controller(self) -> bytes:
        line = self.serial_dep.readline()
        if line:
            return line
        return self.serial_dep.read()

    def write_instrument(self, address: int, command: str) -> int:
        payload = f"++addr {address}\n{command}\n"
        return self.serial_dep.write(payload)

    def read_instrument(self, address: int) -> bytes:
        self.serial_dep.write(f"++addr {address}\n++read eoi\n")
        line = self.serial_dep.readline()
        if line:
            return line
        return self.serial_dep.read()

    def query_instrument(self, address: int, command: str) -> bytes:
        self.write_instrument(address, command)
        time.sleep(self.read_delay_s)
        return self.read_instrument(address)

    def addressed(self, address: int) -> "PrologixAddressedInstrumentDep":
        return PrologixAddressedInstrumentDep(self, address)

    def close(self) -> None:
        self.serial_dep.close()


class PrologixAddressedInstrumentDep(Dependency):
    """GPIB-addressed transport living behind one Prologix controller."""

    def __init__(self, controller: PrologixControllerDep, address: int):
        self.controller = controller
        self.address = address

    def write(self, command: str) -> int:
        return self.controller.write_instrument(self.address, command)

    def read(self) -> bytes:
        return self.controller.read_instrument(self.address)

    def query(self, command: str) -> bytes:
        return self.controller.query_instrument(self.address, command)

