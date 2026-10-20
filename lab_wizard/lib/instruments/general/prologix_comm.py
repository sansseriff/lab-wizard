from __future__ import annotations

from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.serial import SerialDep


class PrologixControllerDep(Dependency):
    """Serial-backed Prologix controller transport.

    On construction, configures the Prologix as controller with auto-read on
    (``++mode 1`` / ``++auto 1``) and a matching ``++read_tmo_ms``. With
    auto-read, a query is a single write+readline: the Prologix handles the
    GPIB read itself and returns the reply on the serial port.

    Timeout invariant: pyserial ``timeout`` must be >= ``++read_tmo_ms`` so
    pyserial never returns while the Prologix is still mid-read. Violating
    this causes cross-address desync (stale bytes bleeding into the next
    iteration's readline).
    """

    def __init__(self, serial_dep: SerialDep, *, timeout_s: float = 0.1):
        self.serial_dep = serial_dep
        self.timeout_s = timeout_s
        self._configure()

    def _configure(self) -> None:
        read_tmo_ms = max(1, int(self.timeout_s * 1000))
        self.serial_dep.write(
            f"++mode 1\n++auto 1\n++read_tmo_ms {read_tmo_ms}\n"
        )

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
        self.serial_dep.write(f"++addr {address}\n")
        line = self.serial_dep.readline()
        if line:
            return line
        return self.serial_dep.read()

    def query_instrument(self, address: int, command: str) -> bytes:
        self.write_instrument(address, command)
        return self.serial_dep.readline()

    def query_block_instrument(self, address: int, command: str) -> bytes:
        """Query for an IEEE-488.2 definite-length block (``#<n><len><data>``).

        Reads exactly ``len`` data bytes, so a block whose data holds its own
        newlines comes back whole, then consumes the terminator that follows.
        Returns the data alone, or whatever arrived instead of a block header.
        """
        self.write_instrument(address, command)
        header = self.serial_dep.read(2)
        if len(header) < 2 or header[:1] != b"#" or not header[1:2].isdigit():
            return header + self.serial_dep.readline()
        length = int(self.serial_dep.read(int(header[1:2])) or 0)
        data = self.serial_dep.read(length)
        self.serial_dep.readline()
        return data

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

    def query_block(self, command: str) -> bytes:
        return self.controller.query_block_instrument(self.address, command)
