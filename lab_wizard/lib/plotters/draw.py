"""Draw a plot spec's rows on matplotlib axes.

The one matplotlib drawing of a plot: the live plot window
(:mod:`~lab_wizard.lib.plotters.window`) and the ``plot.png`` a file saver
writes (:mod:`~lab_wizard.lib.data.run_folder`) both call :func:`draw_plot`,
so a run looks the same on screen and on disk.
"""

from __future__ import annotations

from typing import Any, Mapping

import polars as pl

from lab_wizard.lib.data.plot import PlotSpec, to_series

__all__ = ["axis_label", "draw_plot"]


def axis_label(names: list[str], columns: Mapping[str, Any]) -> str:
    """``"bias_voltage (V), current"``: the names, each with its unit if it has one."""
    parts = []
    for name in names:
        unit = (columns.get(name) or {}).get("unit")
        parts.append(f"{name} ({unit})" if unit else name)
    return ", ".join(parts)


def draw_plot(ax: Any, spec: PlotSpec, rows: pl.DataFrame, columns: Mapping[str, Any]) -> None:
    """Draw ``rows`` (``evaluate_plot``'s) on ``ax``, replacing what it showed.

    ``columns`` is the run's ``{name: {"unit": ...}}``, for the axis labels.
    """
    series_list = to_series(rows)
    ax.clear()
    for twin in [a for a in ax.figure.axes if a is not ax]:
        twin.remove()
    axes = {"y": ax}
    if spec.y2:
        axes["y2"] = ax.twinx()
        axes["y2"].set_ylabel(axis_label(spec.y2, columns))
    if spec.kind == "waterfall":
        for series in series_list:
            ax.scatter(series["x"], series["y"], c=series["z"], marker="s")
    else:
        style = "o" if spec.kind == "scatter" or spec.connect == "none" else "-o"
        for series in series_list:
            axes[series["axis"]].plot(
                series["x"], series["y"], style, markersize=3, label=series["label"] or series["y_name"]
            )
        if len(series_list) > 1:
            ax.legend(loc="best", fontsize="small")
    ax.set_xlabel(axis_label([spec.x], columns))
    ax.set_ylabel(axis_label(spec.y, columns))
    if spec.log_x:
        ax.set_xscale("log")
    if spec.log_y:
        ax.set_yscale("log")
    if spec.x_range:
        ax.set_xlim(*spec.x_range)
    if spec.y_range:
        ax.set_ylim(*spec.y_range)
    ax.grid(True, alpha=0.3)
