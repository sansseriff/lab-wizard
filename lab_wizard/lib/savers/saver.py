from __future__ import annotations

"""
saver.py

Abstract lifecycle-aware base class for savers and a stand-in implementation.

Each measurement run looks like:

    saver.start_run(run_type="iv_curve", device="A7", cryostat="BlueFors-1")
    for integration in ...:
        saver.write_measurement(counts=..., int_time=..., metadata={...})
    saver.end_run()

A simple file-based saver may implement these as open/append/close, while a
DB-backed saver writes a row to ``runs`` on start and a row to ``measurements``
per integration.
"""

from typing import Any, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from lab_wizard.lib.savers.base import SaverParams


class GenericSaver(ABC):
    """Abstract lifecycle-aware base class for all savers."""

    @abstractmethod
    def start_run(
        self,
        *,
        run_type: str,
        device: str | None = None,
        cryostat: str | None = None,
        operator: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Open a new run.  Called once per measurement program invocation."""
        ...

    @abstractmethod
    def write_measurement(
        self,
        *,
        counts: int | None = None,
        int_time: float | None = None,
        delta_time: float | None = None,
        temperature: float | None = None,
        metadata: dict[str, Any] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        """Persist one integration's worth of data."""
        ...

    @abstractmethod
    def end_run(self) -> None:
        """Mark the run finished and flush state."""
        ...

    @classmethod
    @abstractmethod
    def from_params(cls, params: "SaverParams") -> "GenericSaver":
        """Construct a runtime saver from its Params object."""
        ...

    @classmethod
    def from_config(cls, exp: Any, *, key: str) -> "GenericSaver":
        """Look up a saver Params on ``exp.savers`` by name and construct it.

        Uses ``params.create_inst()`` for polymorphic dispatch so callers can
        say ``DatabaseSaver.from_config(...)`` (concrete class) or
        ``GenericSaver.from_config(...)`` (base class) and get the right type.
        """
        params = exp.savers[key]
        return params.create_inst()


class StandInSaver(GenericSaver):
    """A no-op saver. Records lifecycle calls in memory; useful for tests."""

    ignore_in_cli = True

    def __init__(self) -> None:
        self.started: bool = False
        self.ended: bool = False
        self.run_info: dict[str, Any] | None = None
        self.measurements: list[dict[str, Any]] = []
        print("Stand-in saver initialized.")

    def start_run(
        self,
        *,
        run_type: str,
        device: str | None = None,
        cryostat: str | None = None,
        operator: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.started = True
        self.run_info = {
            "run_type": run_type,
            "device": device,
            "cryostat": cryostat,
            "operator": operator,
            "description": description,
            "config": config,
        }
        print(f"Stand-in: start_run {self.run_info}")

    def write_measurement(
        self,
        *,
        counts: int | None = None,
        int_time: float | None = None,
        delta_time: float | None = None,
        temperature: float | None = None,
        metadata: dict[str, Any] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        row = {
            "counts": counts,
            "int_time": int_time,
            "delta_time": delta_time,
            "temperature": temperature,
            "metadata": metadata or {},
            "details": details or [],
        }
        self.measurements.append(row)
        print(f"Stand-in: write_measurement #{len(self.measurements)} keys={list((metadata or {}).keys())}")

    def end_run(self) -> None:
        self.ended = True
        print(f"Stand-in: end_run ({len(self.measurements)} measurements written)")

    @classmethod
    def from_params(cls, params: "SaverParams") -> "StandInSaver":
        return cls()
