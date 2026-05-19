from __future__ import annotations

"""
plotter.py

Abstract base class for plotters and a stand-in implementation.
"""

from typing import Any, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from lab_wizard.lib.plotters.base import PlotterParams


class GenericPlotter(ABC):
    """Abstract base class for all plotters."""

    @abstractmethod
    def plot(self, data: dict[str, Any]) -> None:
        """Render the provided data."""
        ...

    @abstractmethod
    def save_plot(self, filename: str) -> None:
        """Persist the current plot to a file."""
        ...

    @classmethod
    @abstractmethod
    def from_params(cls, params: "PlotterParams") -> "GenericPlotter":
        """Construct a runtime plotter from its Params object."""
        ...

    @classmethod
    def from_config(cls, exp: Any, *, key: str) -> "GenericPlotter":
        """Look up a plotter Params on ``exp.plotters`` by name and construct it.

        Uses ``params.create_inst()`` for polymorphic dispatch so callers can
        say ``MplPlotter.from_config(...)`` (concrete class) or
        ``GenericPlotter.from_config(...)`` (base class) and get the right type.
        """
        params = exp.plotters[key]
        return params.create_inst()


class StandInPlotter(GenericPlotter):
    """A no-op plotter. Stores last payload in memory; useful for tests."""

    ignore_in_cli = True

    def __init__(self) -> None:
        self.plotted_count: int = 0
        self.last_data: dict[str, Any] | None = None
        self.last_saved_filename: str | None = None
        print("Stand-in plotter initialized.")

    def plot(self, data: dict[str, Any]) -> None:
        keys = list(data.keys())
        print(f"Stand-in: Plotting data (no-op). Keys: {keys}")
        self.last_data = data
        self.plotted_count += 1

    def save_plot(self, filename: str) -> None:
        print(f"Stand-in: Saving plot to '{filename}' (no-op)")
        self.last_saved_filename = filename

    @classmethod
    def from_params(cls, params: "PlotterParams") -> "StandInPlotter":
        return cls()
