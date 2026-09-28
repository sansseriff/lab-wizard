"""A matplotlib window following one run in the lab database.

Started by :class:`~lab_wizard.lib.plotters.mpl_plotter.MplPlotter` as a
process of its own::

    python -m lab_wizard.lib.plotters.window --db data/lab.db --run 41 [--plot IV]

It redraws twice a second while the run records, draws once more when it
ends, and stays open until it is closed. Being its own process, it owns its
main thread (which a GUI needs on macOS), and closing it never stops the run.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from lab_wizard.lib.data.plot import PlotSpec, load_plot, run_plots, to_series
from lab_wizard.lib.data.read import Lab

__all__ = ["draw", "main", "run_state"]

POLL_S = 0.5


def run_state(db: Path, run_id: int, plot_name: str = "") -> tuple[dict[str, Any], PlotSpec | None, dict[str, Any]]:
    """The run's row, the plot to draw (named, else its first), and its columns."""
    runs = Lab(db).runs([run_id])
    (summary,) = runs.table().to_dicts()
    info = runs.info(run_id)
    columns = runs.columns()
    plots = run_plots(info["definition"], list(columns))
    spec = next((p for p in plots if p.name == plot_name), plots[0] if plots else None)
    if spec is not None:
        spec = spec.model_copy(update={"runs": [run_id]})
    return summary, spec, columns


def _label(names: list[str], columns: dict[str, Any]) -> str:
    parts = []
    for name in names:
        unit = (columns.get(name) or {}).get("unit")
        parts.append(f"{name} ({unit})" if unit else name)
    return ", ".join(parts)


def draw(ax: Any, spec: PlotSpec, db: Path, columns: dict[str, Any]) -> None:
    """Draw ``spec`` on ``ax``, as the notebook export does."""
    series_list = to_series(load_plot(spec, db))
    ax.clear()
    for twin in [a for a in ax.figure.axes if a is not ax]:
        twin.remove()
    axes = {"y": ax}
    if spec.y2:
        axes["y2"] = ax.twinx()
        axes["y2"].set_ylabel(_label(spec.y2, columns))
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
    ax.set_xlabel(_label([spec.x], columns))
    ax.set_ylabel(_label(spec.y, columns))
    if spec.log_x:
        ax.set_xscale("log")
    if spec.log_y:
        ax.set_yscale("log")
    ax.grid(True, alpha=0.3)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--run", required=True, type=int)
    parser.add_argument("--plot", default="")
    args = parser.parse_args(argv)

    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 5))
    drawn: tuple[Any, ...] | None = None
    while plt.fignum_exists(fig.number):
        summary, spec, columns = run_state(args.db, args.run, args.plot)
        title = f"{summary['procedure']} · run {args.run} · {summary['status']} · {summary['points']} points"
        state = (summary["status"], summary["points"])
        if state != drawn:
            if spec is not None:
                draw(ax, spec, args.db, columns)
                ax.set_title(spec.name or "")
            fig.suptitle(title, fontsize="small")
            if fig.canvas.manager is not None:
                fig.canvas.manager.set_window_title(title)
            fig.canvas.draw_idle()
            drawn = state
        if summary["status"] != "running":
            break
        plt.pause(POLL_S)
    if plt.fignum_exists(fig.number):
        plt.show()  # the run is over: keep the window until it is closed


if __name__ == "__main__":
    main()
