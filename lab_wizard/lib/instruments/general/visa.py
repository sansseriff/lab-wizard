from __future__ import annotations

import atexit
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

try:  # pragma: no cover
    import pyvisa  # type: ignore
except Exception:  # pragma: no cover
    pyvisa = None  # type: ignore

logger = logging.getLogger(__name__)


def _coerce_str(payload: Any) -> str:
    """Normalize payload to string."""
    if isinstance(payload, bytes):
        return payload.decode("utf-8", errors="replace")
    return str(payload)


def _coerce_bytes(payload: Any) -> bytes:
    """Normalize payload to bytes."""
    if isinstance(payload, bytes):
        return payload
    if isinstance(payload, str):
        return payload.encode()
    return str(payload).encode()


class VisaDep(ABC):
    @property
    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def write(self, cmd: str) -> None: ...

    @abstractmethod
    def read(self) -> str: ...

    @abstractmethod
    def read_bytes(self, n: int) -> bytes: ...

    @abstractmethod
    def query(self, cmd: str) -> str: ...

    @abstractmethod
    def clear(self) -> None: ...

    @abstractmethod
    def set_timeout(self, s: float) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


@dataclass
class LocalVisaDep(VisaDep):
    """Local VISA resource dependency using pyvisa."""

    resource: str
    timeout: float = 5.0
    _inst: Any = field(default=None, init=False, repr=False)

    def _ensure(self):
        if self._inst is None:
            if pyvisa is None:  # pragma: no cover
                raise RuntimeError("pyvisa not available")
            rm = pyvisa.ResourceManager("@py")
            self._inst = rm.open_resource(self.resource)
            self._inst.timeout = int(self.timeout * 1000)
            logger.debug("Opened VISA resource %s", self.resource)
            atexit.register(self.close)
        return self._inst

    @property
    def is_open(self) -> bool:
        return self._inst is not None

    def write(self, cmd: str) -> None:
        self._ensure().write(cmd)

    def read(self) -> str:
        payload = self._ensure().read()
        return _coerce_str(payload)

    def read_bytes(self, n: int) -> bytes:
        return _coerce_bytes(self._ensure().read_bytes(n))

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read()

    def clear(self) -> None:
        inst = self._ensure()
        try:
            inst.clear()
        except Exception:
            pass

    def set_timeout(self, s: float) -> None:
        self._ensure().timeout = int(s * 1000)

    def close(self) -> None:
        if self._inst is not None:
            try:
                self._inst.close()
                logger.debug("Closed VISA resource %s", self.resource)
            except Exception:
                pass
            self._inst = None

    def __del__(self) -> None:
        # Owning dep for the pyvisa handle — release here; aliasing is not
        # possible since _inst is only constructed inside this object.
        try:
            self.close()
        except Exception:
            pass
