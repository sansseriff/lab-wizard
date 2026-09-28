"""Live views of a run: plotters, the live feed, and its websocket.

Every live view reads the lab database the run is recording into, so these
tests record real runs into a project's database and watch them — finished,
and still going.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from lab_procedure import Sequence, Status, Step, Sweep, WithParameter
from lab_wizard.lib.plotters import MplPlotter, StandInPlotter, WebPlotter
from lab_wizard.lib.task_adapters.run import LAUNCH_FILE_ENV, project_outputs, run_procedure
from lab_wizard.wizard.backend.live import LiveFeed
from lab_wizard.wizard.backend.live_server import live_app

DEFINITION: dict[str, Any] = {
    "name": "probe",
    "body": {"type": "wait", "seconds": 0},
    "plots": [
        {"name": "Counts", "x": "bias", "y": ["counts"], "where": {"phase": "signal"}},
        {"name": "Voltage", "x": "bias", "y": ["voltage"]},
    ],
}


class Measure(Step):
    def __init__(self, pause: threading.Event | None = None, **fields: Any) -> None:
        super().__init__()
        self.fields = fields
        self.pause = pause

    def run(self) -> Status:
        assert self.context is not None
        if self.pause is not None:
            self.pause.wait(timeout=10)
        p = self.context.parameters
        self.context.observe({k: f(p) if callable(f) else f for k, f in self.fields.items()})
        return Status.SUCCESS


def tree(pause: threading.Event | None = None) -> Step:
    """A background point, then a sweep; with ``pause``, the last point waits for it."""
    return Sequence(
        WithParameter("phase", "background", Measure(counts=3.0)),
        WithParameter("phase", "signal", Sweep("bias", [0.1, 0.2, 0.3], lambda b: Measure(
            pause=pause if b == 0.3 else None, counts=lambda p: 1000 * p["bias"], voltage=lambda p: p["bias"] / 2,
        ))),
    )


@dataclass
class Resources:
    params: Any = None


def _project(tmp_path: Path, outputs: str = "{files: false}") -> Path:
    project_dir = tmp_path / "probe_1"
    project_dir.mkdir(parents=True)
    (project_dir / "probe_1.yaml").write_text(
        f"project: {{measurement_type: probe}}\nrun: {{device: A7}}\noutputs: {outputs}\n", encoding="utf-8"
    )
    return project_dir


def _run(project_dir: Path, **kwargs: Any) -> Status:
    return run_procedure(tree(kwargs.pop("pause", None)), Resources(), procedure="probe", definition=DEFINITION,
                         project_dir=project_dir, **kwargs)


# --------------------------- plotters ---------------------------


def test_a_plotter_is_told_where_the_run_is_recorded_and_when_it_ends(tmp_path: Path):
    project_dir = _project(tmp_path)
    plotter = StandInPlotter()
    assert _run(project_dir, plotters=[plotter]) is Status.SUCCESS
    assert plotter.started == (project_dir / "data" / "lab.db", 1)
    assert plotter.ended == "success"
    assert plotter.finished


def test_a_plotter_that_fails_does_not_fail_the_run(tmp_path: Path, caplog):
    class Broken(StandInPlotter):
        def run_started(self, database: Path, run_id: int) -> None:
            raise RuntimeError("no screen")

    assert _run(_project(tmp_path), plotters=[Broken()]) is Status.SUCCESS
    assert "Broken failed; the run continues" in caplog.text


def test_a_project_picks_its_live_plot(tmp_path: Path):
    _savers, [window] = project_outputs(_project(tmp_path / "a", "{files: false, live_plot: window, plot: Voltage}"))
    assert isinstance(window, MplPlotter)
    assert window.command(Path("lab.db"), 4)[-6:] == ["--db", "lab.db", "--run", "4", "--plot", "Voltage"]
    _savers, [web] = project_outputs(_project(tmp_path / "b", "{files: false, live_plot: web}"))
    assert isinstance(web, WebPlotter)


def test_a_run_the_wizard_launched_opens_no_plot_and_reports_its_id(tmp_path: Path, monkeypatch):
    launch_file = tmp_path / "launch.json"
    monkeypatch.setenv(LAUNCH_FILE_ENV, str(launch_file))
    project_dir = _project(tmp_path, "{files: false, live_plot: window}")
    assert project_outputs(project_dir) == ([], [])
    assert _run(project_dir) is Status.SUCCESS
    assert json.loads(launch_file.read_text()) == {"run_id": 1, "database": str(project_dir / "data" / "lab.db")}


@pytest.mark.filterwarnings("ignore:FigureCanvasAgg is non-interactive")
def test_the_plot_window_draws_a_recorded_run(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MPLBACKEND", "Agg")
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from lab_wizard.lib.plotters import window

    project_dir = _project(tmp_path)
    _run(project_dir)
    db = project_dir / "data" / "lab.db"

    summary, spec, columns = window.run_state(db, 1, "Voltage")
    assert (summary["status"], spec.name if spec else None) == ("success", "Voltage")
    fig, ax = plt.subplots()
    window.draw(ax, spec, db, columns)
    (line,) = ax.get_lines()
    assert list(line.get_xdata()) == [0.1, 0.2, 0.3]
    plt.close(fig)

    window.main(["--db", str(db), "--run", "1"])  # a finished run: draws once and returns


# --------------------------- the live feed ---------------------------


def _drain(feed: LiveFeed) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for _ in range(50):
        messages += feed.poll()
        if feed.ended:
            return messages
        time.sleep(0.05)
    raise AssertionError("the feed never ended")


def test_a_finished_run_is_sent_whole_then_ended(tmp_path: Path):
    project_dir = _project(tmp_path)
    _run(project_dir)
    messages = _drain(LiveFeed(project_dir / "data" / "lab.db", tmp_path / "config", 1))

    assert [m["type"] for m in messages] == ["run", "status", "steps", "plots", "end"]
    run, status, steps, plots = messages[:4]
    assert [p["name"] for p in run["plots"]] == ["Counts", "Voltage"]
    assert status["run"]["status"] == "success" and status["run"]["points"] == 4
    assert steps["steps"][0]["path"] == "sequence" and all(s["ended_at"] for s in steps["steps"])
    counts = plots["plots"][0]
    assert counts["name"] == "Counts" and counts["series"][0]["y"] == [100.0, 200.0, 300.0]


def test_a_running_run_is_followed_as_it_grows(tmp_path: Path):
    project_dir = _project(tmp_path)
    pause = threading.Event()
    thread = threading.Thread(target=_run, args=(project_dir,), kwargs={"pause": pause})
    thread.start()
    try:
        db = project_dir / "data" / "lab.db"
        deadline = time.monotonic() + 5
        while not db.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        feed = LiveFeed(db, tmp_path / "config", 1)
        seen: list[dict[str, Any]] = []
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            seen += feed.poll()
            if any(m["type"] == "plots" and m["plots"][0]["series"] for m in seen):
                break
            time.sleep(0.05)
        # Mid-run: the run is going, and the points so far are already drawn.
        status = [m for m in seen if m["type"] == "status"][-1]["run"]
        assert status["status"] == "running"
        open_steps = [s for m in seen if m["type"] == "steps" for s in m["steps"] if s["ended_at"] is None]
        # The whole branch down to the waiting step is open: the live hierarchy.
        assert [s["path"] for s in open_steps] == [
            "sequence",
            "sequence/with_parameter[1]",
            "sequence/with_parameter[1]/sweep[0]",
            "sequence/with_parameter[1]/sweep[0]/measure#2",
        ]
        assert not feed.ended
    finally:
        pause.set()
        thread.join(timeout=10)

    rest = _drain(feed)
    assert rest[-1] == {"type": "end"}
    # The steps that were open are sent again, now ended.
    ended = {s["id"] for m in rest if m["type"] == "steps" for s in m["steps"] if s["ended_at"]}
    assert {s["id"] for s in open_steps} <= ended
    assert [m for m in rest if m["type"] == "plots"][-1]["plots"][0]["series"][0]["y"] == [100.0, 200.0, 300.0]


def test_the_live_websocket_streams_a_run(tmp_path: Path):
    project_dir = _project(tmp_path)
    _run(project_dir)
    client = TestClient(live_app(project_dir / "data" / "lab.db"))
    with client.websocket_connect("/api/live/runs/1") as ws:
        types = []
        while True:
            message = ws.receive_json()
            types.append(message["type"])
            if message["type"] == "end":
                break
    assert types == ["run", "status", "steps", "plots", "end"]

    with client.websocket_connect("/api/live/runs/99") as ws:
        assert ws.receive_json()["type"] == "error"
