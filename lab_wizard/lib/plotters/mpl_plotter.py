from __future__ import annotations

"""
Matplotlib-based plotter. Scaffolding only — the runtime currently delegates
to StandInPlotter behavior.  Real rendering lands in a follow-up.
"""

from typing import Any, Literal

from pydantic import Field

from lab_wizard.lib.plotters.base import PlotterParams
from lab_wizard.lib.plotters.plotter import GenericPlotter


class MplPlotterParams(PlotterParams):
    """Params for the local matplotlib-backed plotter."""

    type: Literal["mpl_plotter"] = "mpl_plotter"
    figure_size: tuple[int, int] = Field(default=(8, 6), description="(width, height) in inches")
    dpi: int = Field(default=100, description="Dots per inch for rendering")

    @property
    def inst(self) -> type["MplPlotter"]:
        return MplPlotter


class MplPlotter(GenericPlotter):
    """Local matplotlib plotter (placeholder implementation)."""

    def __init__(self, figure_size: tuple[int, int] = (8, 6), dpi: int = 100) -> None:
        self.figure_size = figure_size
        self.dpi = dpi
        self.last_data: dict[str, Any] | None = None
        self.last_saved_filename: str | None = None

    @classmethod
    def from_params(cls, params: PlotterParams) -> "MplPlotter":
        if not isinstance(params, MplPlotterParams):
            raise TypeError(
                f"MplPlotter.from_params expected MplPlotterParams, got {type(params).__name__}"
            )
        return cls(figure_size=params.figure_size, dpi=params.dpi)

    def plot(self, data: dict[str, Any]) -> None:
        self.last_data = data
        print(
            f"MplPlotter: plot called (size={self.figure_size}, dpi={self.dpi}, "
            f"keys={list(data.keys())})"
        )

    def save_plot(self, filename: str) -> None:
        self.last_saved_filename = filename
        print(f"MplPlotter: save_plot to '{filename}'")
