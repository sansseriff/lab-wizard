from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, Any

from lab_wizard.lib.instruments.general.parent_child import Dependency

try:
    import serial as pyserial  # type: ignore
except Exception:  # pragma: no cover - tests patch serial anyway
    pyserial = None  # type: ignore


def _ensure_bytes(data: bytes | str) -> bytes:
    """Convert str to bytes if needed."""
    if isinstance(data, bytes):
        return data
    return data.encode()


class SerialDep(Dependency, ABC):
    """Abstract serial-like dependency API.

    Concrete implementation: LocalSerialDep
    """

    @property
    @abstractmethod
    def is_open(self) -> bool:  # pragma: no cover - interface
        ...

    @abstractmethod
    def write(self, data: bytes | str) -> int: ...  # returns bytes written

    @abstractmethod
    def read(self, size: Optional[int] = None) -> bytes: ...

    @abstractmethod
    def readline(self) -> bytes: ...

    def query(self, cmd: str) -> bytes:
        self.write(cmd)
        return self.readline()

    @abstractmethod
    def close(self) -> None: ...


@dataclass
class LocalSerialDep(SerialDep):
    """Local serial port dependency using pyserial."""

    port: str
    baudrate: int = 9600
    timeout: float = 1.0
    _serial: Any = field(default=None, init=False, repr=False)

    def _ensure(self) -> Any:
        if self._serial is None:
            if pyserial is None:  # pragma: no cover
                raise RuntimeError("serial module not available")
            self._serial = pyserial.Serial(
                port=self.port, baudrate=self.baudrate, timeout=self.timeout
            )
        return self._serial

    @property
    def is_open(self) -> bool:
        return bool(self._serial and getattr(self._serial, "is_open", False))

    def write(self, data: bytes | str) -> int:
        ser = self._ensure()
        return ser.write(_ensure_bytes(data))  # type: ignore[no-any-return]

    def read(self, size: Optional[int] = None) -> bytes:
        ser = self._ensure()
        if size is None:
            try:
                return ser.read_all()
            except Exception:  # pragma: no cover
                return ser.read(9999)
        return ser.read(size)

    def readline(self) -> bytes:
        return self._ensure().readline()

    def close(self) -> None:
        if self._serial and getattr(self._serial, "is_open", False):
            self._serial.close()
