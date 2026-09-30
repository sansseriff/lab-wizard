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

from lab_wizard.lib.data.plot import PlotSpec, load_plot, run_plots
from lab_wizard.lib.data.read import Lab
from lab_wizard.lib.plotters.draw import draw_plot

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


def draw(ax: Any, spec: PlotSpec, db: Path, columns: dict[str, Any]) -> None:
    """Draw ``spec`` on ``ax`` from the run as recorded so far."""
    draw_plot(ax, spec, load_plot(spec, db), columns)


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
