"""A run as a folder of plain files: the format a file saver writes.

::

    2026-09-22/mcr_curve_A7_143012/
      run.yaml          everything about the run: procedure, device, times, params,
                        instruments, columns with units
      procedure.yaml    the procedure definition it ran
      points.csv        one line per point: seq, t, a column per value, steps
      steps.csv         every step it executed: path, kind, times, status, error
      <array>.csv       one per array column (a histogram): seq, then one column per bin
      plot.png          optionally, the run's default plot

The folder is a complete copy of the run's rows in the lab database
(``plans/semantic_data_plan.md`` §7, D15). One writer serves a live run
(:class:`~lab_wizard.lib.savers.file_saver.FileSaver`) and a recorded one
(:func:`export_run`), so the two produce the same files.

While a run is going, ``points.csv`` and ``steps.csv`` are appended to and
flushed a line at a time, so a crash loses at most the line being written.
When it ends, every file is written once more in its final form: columns first
seen during the run get their place in the header, steps are in the order they
started, and array columns move to their own files.
"""

from __future__ import annotations

import csv
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from lab_wizard.lib.data.encoding import jsonable

__all__ = ["FOLDER_KEYS", "FOLDER_KEY_FAMILIES", "RunFolder", "check_template", "export_run", "folder_name", "run_facets_for_folder"]

# What a run.yaml holds, in order: the runs row, as the database has it.
RUN_FIELDS = (
    "run_id", "procedure", "status", "started_at", "ended_at", "device", "operator",
    "notes", "project", "metadata", "params", "instruments", "columns",
)
STEP_FIELDS = ("path", "kind", "started_at", "ended_at", "status", "error")

_FIELD = re.compile(r"\{([^{}]+)\}")

# What a folder template can name: every run has these ...
FOLDER_KEYS = ("date", "time", "procedure", "device", "operator", "project", "status", "run_id")
# ... and these families hold whatever a lab records: device properties, the
# run: block's metadata, params, and instrument settings.
FOLDER_KEY_FAMILIES = ("device.", "run.", "param.", "instrument.")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _cell(value: Any) -> str:
    """A CSV cell. A missing reading is an empty cell, as are NaN and infinity."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value)


def _segment(text: str) -> str:
    cleaned = _UNSAFE.sub("_", text).strip().lstrip(".")
    return cleaned or "none"


def folder_name(template: str, facets: Mapping[str, str]) -> Path:
    """The relative folder ``template`` names for a run with these facets.

    ``{date}/{procedure}_{device}_{time}`` with ``device = A7`` gives
    ``2026-09-22/mcr_curve_A7_143012``. A facet the run lacks is ``none``; a
    value that would be unsafe in a path has those characters replaced.
    """
    parts = []
    for part in template.split("/"):
        rendered = _FIELD.sub(lambda m: facets.get(m.group(1).strip(), "none"), part)
        if rendered.strip():
            parts.append(_segment(rendered))
    return Path(*parts) if parts else Path("run")


def check_template(template: str, recorded: set[str] | None = None) -> list[dict[str, str]]:
    """What is wrong with a folder template, if anything.

    Each problem is ``{"level": "error" | "warning", "key", "message"}``. A key
    no run can have is an error. A key in one of the open families that no run
    has recorded yet is only a warning — the lab may start recording it — so
    ``recorded`` (the keys the lab database has seen) is optional.
    """
    problems: list[dict[str, str]] = []
    if not template.strip():
        return [{"level": "error", "key": "", "message": "The template cannot be empty."}]
    if template.count("{") != template.count("}"):
        problems.append({"level": "error", "key": "", "message": "A { has no matching }, or a } no matching {."})
    for match in _FIELD.finditer(template):
        key = match.group(1).strip()
        family = next((f for f in FOLDER_KEY_FAMILIES if key.startswith(f) and len(key) > len(f)), None)
        if key in FOLDER_KEYS:
            continue
        if family is None:
            problems.append({"level": "error", "key": key, "message": f"No run has a {{{key}}}."})
        elif recorded is not None and key not in recorded:
            problems.append({
                "level": "warning",
                "key": key,
                "message": f"No run has recorded {{{key}}} yet; runs without it go in a folder named none.",
            })
    return problems


def _local(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp).astimezone()


class RunFolder:
    """Writes one run's folder: start it, add points and steps, finish it."""

    def __init__(self, path: Path, run: Mapping[str, Any], definition: Mapping[str, Any] | None) -> None:
        self.path = path
        self.path.mkdir(parents=True, exist_ok=False)
        self.run = dict(run)
        self.definition = definition
        self.points: list[dict[str, Any]] = []
        self.steps: dict[Any, dict[str, Any]] = {}
        self._write_yaml("run.yaml", self._run_yaml(self.run))
        if definition is not None:
            self._write_yaml("procedure.yaml", jsonable(definition))
        # Written as the run goes, for a crash; rewritten whole at the end.
        self._points_header = self._point_header([])
        self._points_file = (self.path / "points.csv").open("w", newline="", encoding="utf-8")
        self._points_csv = csv.writer(self._points_file)
        self._points_csv.writerow(self._points_header)
        self._points_file.flush()
        self._steps_file = (self.path / "steps.csv").open("w", newline="", encoding="utf-8")
        self._steps_csv = csv.writer(self._steps_file)
        self._steps_csv.writerow(STEP_FIELDS)
        self._steps_file.flush()

    @classmethod
    def create(
        cls,
        root: Path,
        template: str,
        facets: Mapping[str, str],
        run: Mapping[str, Any],
        definition: Mapping[str, Any] | None,
    ) -> "RunFolder":
        """A new folder under ``root`` named by ``template``, never overwriting one."""
        base = root / folder_name(template, facets)
        path = base
        for n in range(2, 10_000):
            if not path.exists():
                break
            path = base.with_name(f"{base.name}_{n}")
        return cls(path, run, definition)

    # ------------------------------------------------------------ writing

    def add_point(self, seq: int, t: str, steps: list[str], values: Mapping[str, Any]) -> None:
        point = {"seq": seq, "t": t, "steps": list(steps), "values": jsonable(dict(values))}
        self.points.append(point)
        self._points_csv.writerow(self._point_row(point, self._points_header))
        self._points_file.flush()

    def step_began(self, key: Any, path: str, kind: str, started_at: str) -> None:
        self.steps[key] = {"path": path, "kind": kind, "started_at": started_at,
                           "ended_at": None, "status": None, "error": None}

    def step_ended(self, key: Any, ended_at: str, status: str, error: str | None) -> None:
        step = self.steps.get(key)
        if step is None:
            return
        step.update(ended_at=ended_at, status=status, error=error)
        self._steps_csv.writerow([_cell(step[f]) for f in STEP_FIELDS])
        self._steps_file.flush()

    def finish(self, run: Mapping[str, Any], *, plot_png: bool = False) -> None:
        """Write every file in its final form."""
        self.run = dict(run)
        # As the database records it: the declared columns, then any recorded
        # column nobody declared, in the order they arrived.
        columns = dict(self.run.get("columns") or {})
        for name in self._columns():
            columns.setdefault(name, {"unit": None})
        self.run["columns"] = columns
        self._points_file.close()
        self._steps_file.close()
        arrays = self._array_columns()
        self._write_yaml("run.yaml", self._run_yaml(self.run))

        header = self._point_header(arrays)
        with (self.path / "points.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(self._point_row(p, header) for p in self.points)

        with (self.path / "steps.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(STEP_FIELDS)
            writer.writerows([_cell(step[field]) for field in STEP_FIELDS] for step in self.steps.values())

        for name in arrays:
            self._write_array(name)
        if plot_png:
            self._write_plot()

    # ------------------------------------------------------------ helpers

    def _run_yaml(self, run: Mapping[str, Any]) -> dict[str, Any]:
        return {field: jsonable(run.get(field)) for field in RUN_FIELDS}

    def _write_yaml(self, name: str, data: Any) -> None:
        with (self.path / name).open("w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)

    def _columns(self) -> list[str]:
        names = list((self.run.get("columns") or {}).keys())
        for point in self.points:
            for name in point["values"]:
                if name not in names:
                    names.append(name)
        return names

    def _array_columns(self) -> list[str]:
        return [n for n in self._columns() if any(isinstance(p["values"].get(n), list) for p in self.points)]

    def _point_header(self, arrays: list[str]) -> list[str]:
        return ["seq", "t", *[n for n in self._columns() if n not in arrays], "steps"]

    @staticmethod
    def _point_row(point: Mapping[str, Any], header: list[str]) -> list[str]:
        row = []
        for name in header:
            if name in ("seq", "t"):
                row.append(_cell(point[name]))
            elif name == "steps":
                row.append(";".join(point["steps"]))
            else:
                value = point["values"].get(name)
                row.append("" if isinstance(value, list) else _cell(value))
        return row

    def _write_array(self, name: str) -> None:
        rows = [(p["seq"], p["values"][name]) for p in self.points if isinstance(p["values"].get(name), list)]
        width = max((len(values) for _seq, values in rows), default=0)
        bins = ((self.run.get("columns") or {}).get(name) or {}).get("bins") or {}
        start, step = bins.get("start", 0), bins.get("step", 1)
        with (self.path / f"{_segment(name)}.csv").open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["seq", *[_cell(start + i * step) for i in range(width)]])
            writer.writerows([_cell(seq), *[_cell(v) for v in values]] for seq, values in rows)

    def _write_plot(self) -> None:
        """The run's default plot, drawn without a display."""
        import polars as pl
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        from lab_wizard.lib.data.plot import default_plot, evaluate_plot, to_series

        spec = default_plot(self.definition, self._columns())
        if spec is None or not self.points:
            return
        frame = pl.DataFrame(
            [{"run_id": 0, "seq": p["seq"], **p["values"]} for p in self.points],
            infer_schema_length=None,
            strict=False,
        )
        derived = (self.definition or {}).get("derived") or {}
        rows = evaluate_plot(spec, frame, params={0: self.run.get("params") or {}}, derived=derived)
        figure = Figure(figsize=(7, 4.5))
        FigureCanvasAgg(figure)
        axes = {"y": figure.add_subplot()}
        if spec.y2:
            axes["y2"] = axes["y"].twinx()
        for series in to_series(rows):
            style = "o" if spec.kind == "scatter" or spec.connect == "none" else "-o"
            axes[series["axis"]].plot(series["x"], series["y"], style, label=series["label"] or series["y_name"], markersize=3)
        axes["y"].set_xlabel(spec.x)
        axes["y"].set_ylabel(", ".join(spec.y))
        if spec.log_x:
            axes["y"].set_xscale("log")
        if spec.log_y:
            axes["y"].set_yscale("log")
        axes["y"].set_title(spec.name or f"{self.run.get('procedure')} · {self.run.get('device') or 'no device'}")
        if len(spec.y) + len(spec.y2) > 1:
            figure.legend()
        figure.tight_layout()
        figure.savefig(self.path / "plot.png", dpi=120)


def run_facets_for_folder(run: Mapping[str, Any], device: Mapping[str, Any] | None) -> dict[str, str]:
    """The values a folder template can name: every facet, plus ``time`` and ``run_id``."""
    from lab_wizard.lib.data.facets import run_facets

    out: dict[str, str] = {}
    for key, value, _num in run_facets(run, device):
        out.setdefault(key, value)  # a many-valued facet ("column") names its first
    if run.get("started_at"):
        out["time"] = _local(run["started_at"]).strftime("%H%M%S")
    if run.get("run_id") is not None:
        out["run_id"] = str(run["run_id"])
    return out


def export_run(
    db: str | Path,
    run_id: int,
    root: str | Path,
    template: str = "{date}/{procedure}_{device}_{time}",
    *,
    plot_png: bool = True,
) -> Path:
    """Write a recorded run as a folder under ``root``; returns the folder."""
    import json

    from lab_wizard.lib.data.read import Lab

    lab = Lab(db)
    rows = lab.query(
        "SELECT r.*, d.name AS device, d.properties AS device_properties FROM runs r "
        "LEFT JOIN devices d ON d.id = r.device_id WHERE r.id = ?",
        (run_id,),
    )
    if not rows:
        raise KeyError(f"No run with id {run_id} in {db}")
    row = dict(rows[0])
    run = {field: row.get(field) for field in RUN_FIELDS if field != "run_id"}
    run["run_id"] = run_id
    for key in ("metadata", "params", "instruments", "columns"):
        run[key] = json.loads(row[key]) if row[key] else {}
    definition = json.loads(row["definition"]) if row["definition"] else None
    device = {"name": row["device"], "properties": json.loads(row["device_properties"] or "{}")} if row["device"] else None

    folder = RunFolder.create(Path(root), template, run_facets_for_folder(run, device), {**run, "status": "running", "ended_at": None}, definition)
    for step_id, path, kind, started_at, ended_at, status, error in lab.query(
        "SELECT id, path, kind, started_at, ended_at, status, error FROM steps WHERE run_id = ? ORDER BY id", (run_id,)
    ):
        folder.step_began(step_id, path, kind, started_at)
        if ended_at is not None:
            folder.step_ended(step_id, ended_at, status, error)
    for seq, t, steps, values in lab.query(
        'SELECT seq, t, steps, "values" FROM points WHERE run_id = ? ORDER BY seq', (run_id,)
    ):
        folder.add_point(seq, t, json.loads(steps), json.loads(values))
    folder.finish(run, plot_png=plot_png)
    return folder.path
