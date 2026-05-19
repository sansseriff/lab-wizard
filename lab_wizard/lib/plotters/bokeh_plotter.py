from __future__ import annotations

"""
Web-based plotter (Bokeh). Scaffolding only — runtime is a placeholder.
"""

from typing import Any, Literal

from pydantic import Field

from lab_wizard.lib.plotters.base import PlotterParams
from lab_wizard.lib.plotters.plotter import GenericPlotter


class BokehPlotterParams(PlotterParams):
    """Params for the web-based Bokeh plotter."""

    type: Literal["bokeh_plotter"] = "bokeh_plotter"
    url: str = Field(
        default="http://localhost:5006",
        description="URL of the Bokeh server hosting the plot.",
    )
    figure_size: tuple[int, int] = Field(default=(800, 600), description="(width, height) in pixels")

    @property
    def inst(self) -> type["BokehPlotter"]:
        return BokehPlotter


class BokehPlotter(GenericPlotter):
    """Web-based Bokeh plotter (placeholder implementation)."""

    def __init__(self, url: str, figure_size: tuple[int, int] = (800, 600)) -> None:
        self.url = url
        self.figure_size = figure_size
        self.last_data: dict[str, Any] | None = None
        self.last_saved_filename: str | None = None

    @classmethod
    def from_params(cls, params: PlotterParams) -> "BokehPlotter":
        if not isinstance(params, BokehPlotterParams):
            raise TypeError(
                f"BokehPlotter.from_params expected BokehPlotterParams, got {type(params).__name__}"
            )
        return cls(url=params.url, figure_size=params.figure_size)

    def plot(self, data: dict[str, Any]) -> None:
        self.last_data = data
        print(
            f"BokehPlotter: plot called (url={self.url}, size={self.figure_size}, "
            f"keys={list(data.keys())})"
        )

    def save_plot(self, filename: str) -> None:
        self.last_saved_filename = filename
        print(f"BokehPlotter: save_plot to '{filename}'")
