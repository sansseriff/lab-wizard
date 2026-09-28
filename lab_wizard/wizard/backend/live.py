"""A running run, as a stream of changes, for the live views.

The run process records every point and every step in the lab database as it
happens (``lab_wizard.lib.data.recorder``); the database is the bus. A
:class:`LiveFeed` watches one run there and says what changed since it last
looked, as messages a page can apply:

``{"type": "run", ...}``
    once, first: everything the Data page knows about the run (``run_detail``),
    including the plots it is drawn with.
``{"type": "status", "run": {...}}``
    the run's row, whenever its status or point count changes.
``{"type": "steps", "steps": [...]}``
    steps that started, or ended, since the last message; each has its ``id``,
    so a page merges them into the timeline it has.
``{"type": "plots", "plots": [{"name", "series", "units", "shape"}, ...]}``
    every plot, recomputed over all the points so far — never appended, so a
    per-run reduction or a derived column stays right as the run grows. At most
    a few times a second.
``{"type": "end"}``
    last: the run is over and everything about it has been sent.

The same feed serves the wizard's Run page and the standalone live page a web
plotter opens, so both show exactly what the Data page will show afterwards.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from lab_wizard.lib.data.read import Lab
from lab_wizard.wizard.backend import data_api

__all__ = ["LiveFeed"]

PLOT_INTERVAL_S = 0.4


class LiveFeed:
    """What changed in one run since the last :meth:`poll`."""

    def __init__(self, db: Path, config_dir: str | Path, run_id: int) -> None:
        self.db = db
        self.config_dir = config_dir
        self.run_id = run_id
        self.detail: dict[str, Any] | None = None
        self.state: tuple[Any, ...] | None = None
        self.last_step = 0
        self.open_steps: set[int] = set()
        self.plotted_points = -1
        self.plotted_at = 0.0
        self.ended = False

    def poll(self) -> list[dict[str, Any]]:
        if self.ended:
            return []
        messages: list[dict[str, Any]] = []
        lab = Lab(self.db)
        try:
            if self.detail is None:
                self.detail = data_api.run_detail(self.db, self.config_dir, self.run_id)
                messages.append({"type": "run", **self.detail})

            (summary,) = lab.runs([self.run_id]).table().to_dicts()
            state = (summary["status"], summary["points"], summary["ended_at"])
            if state != self.state:
                self.state = state
                messages.append({"type": "status", "run": summary})

            steps = self._new_steps(lab)
            if steps:
                messages.append({"type": "steps", "steps": steps})

            running = summary["status"] == "running"
            due = time.monotonic() - self.plotted_at >= PLOT_INTERVAL_S
            if summary["points"] != self.plotted_points and (due or not running):
                messages.append({"type": "plots", "plots": self._plots()})
                self.plotted_points = summary["points"]
                self.plotted_at = time.monotonic()

            if not running and self.plotted_points == summary["points"] and not self.open_steps:
                self.ended = True
                messages.append({"type": "end"})
        finally:
            lab.close()
        return messages

    def _new_steps(self, lab: Lab) -> list[dict[str, Any]]:
        """Steps started since the last poll, and open ones that have ended."""
        watch = sorted(self.open_steps)
        rows = lab.query(
            f"""SELECT id, path, kind, started_at, ended_at, status, error FROM steps
                WHERE run_id = ? AND (id > ? OR id IN ({", ".join("?" * len(watch)) or "NULL"}))
                ORDER BY id""",
            (self.run_id, self.last_step, *watch),
        )
        out = []
        for row in rows:
            step = dict(row)
            if step["id"] in self.open_steps and step["ended_at"] is None:
                continue  # still going: nothing new to say about it
            out.append(step)
            self.last_step = max(self.last_step, step["id"])
            if step["ended_at"] is None:
                self.open_steps.add(step["id"])
            else:
                self.open_steps.discard(step["id"])
        return out

    def _plots(self) -> list[dict[str, Any]]:
        out = []
        for spec in (self.detail or {}).get("plots", []):
            try:
                drawn = data_api.plot(self.db, self.config_dir, spec)
            except data_api.DataRequestError as e:
                drawn = {"series": [], "units": {}, "shape": {"lines": 0, "points": [0, 0]}, "error": str(e)}
            out.append({"name": spec.get("name"), "spec": spec, **drawn})
        return out
