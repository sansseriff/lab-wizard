"""What the Data page reads and writes: the lab database, for a browser.

The page asks five kinds of question: which filters there are (the sidebar),
which runs match them (the run list), what one run was (details and its
timeline), what a plot spec draws, and how to take a plot or a run somewhere
else (a notebook, a folder of files). It also keeps the device registry, whose
properties are filters on every run of that device, saves a plot the page
built back into its procedure, and keeps the workspace's file-saving settings.

Plots and derived columns come from the procedure's **current** definition, so
a plot or a derived column added to a procedure applies to the runs recorded
before it; a run whose procedure has since been deleted falls back to the
definition it recorded (``plans/semantic_data_plan.md`` §9).

Every function takes the database path, and the workspace's config directory
where it needs procedures, so the routes in ``main.py`` stay thin and tests can
call these directly.
"""

from __future__ import annotations

import io
import json
import tempfile
import zipfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from lab_wizard.lib.data.facets import metadata_units, write_run_facets
from lab_wizard.lib.data.plot import (
    PlotSpec,
    line_shape,
    load_plot,
    notebook_source,
    run_plots,
    to_series,
)
from lab_wizard.lib.data.read import Lab
from lab_wizard.lib.data.run_folder import (
    FOLDER_KEY_FAMILIES,
    FOLDER_KEYS,
    check_template,
    export_run,
    folder_name,
)
from lab_wizard.lib.data.schema import DATABASE_NAME, open_database
from lab_wizard.lib.data.settings import (
    FileSettings,
    load_data_settings,
    save_data_settings,
)
from lab_wizard.lib.procedures.storage import (
    load_procedure,
    procedure_origin,
    save_procedure,
)

__all__ = [
    "DataRequestError",
    "check_file_template",
    "device_list",
    "export_zip",
    "facet_list",
    "file_settings",
    "notebook",
    "parse_filters",
    "plot",
    "point_detail",
    "run_detail",
    "run_list",
    "run_steps",
    "save_device",
    "save_file_settings",
    "save_plot_to_procedure",
]

# The sidebar's sections, in order, and the facet keys each holds: an exact key,
# or a prefix ending in a dot.
FACET_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Procedure", ("procedure",)),
    ("Device", ("device",)),
    ("Device properties", ("device.",)),
    ("Run", ("run.",)),
    ("Instruments", ("instrument.",)),
    ("Params", ("param.",)),
    ("Operator", ("operator",)),
    ("Project", ("project",)),
    ("Date", ("date",)),
    ("Status", ("status",)),
    ("Columns", ("column",)),
]


class DataRequestError(ValueError):
    """A request the page can fix: a bad filter, spec or device. HTTP 422."""


def _group(key: str) -> tuple[int, str]:
    for index, (name, keys) in enumerate(FACET_GROUPS):
        if any(key.startswith(k) if k.endswith(".") else key == k for k in keys):
            return index, name
    return len(FACET_GROUPS), "Other"


def _lab(db: Path) -> Lab | None:
    """The lab database, or ``None`` before the first run has created it."""
    return Lab(db) if db.is_file() else None


def parse_filters(text: str | None) -> dict[str, Any]:
    """Filters as the page sends them: a JSON object of facet key to value.

    A value is a value, a list (any of them), or ``{"range": [lo, hi]}``.
    """
    if not text:
        return {}
    try:
        filters = json.loads(text)
    except json.JSONDecodeError as e:
        raise DataRequestError(f"filters are not JSON: {e}") from e
    if not isinstance(filters, dict):
        raise DataRequestError("filters must be an object of facet key to value")
    return filters


# --------------------------- the sidebar and the run list ---------------------------


def facet_list(db: Path, filters: Mapping[str, Any]) -> dict[str, Any]:
    """Every filter with its values, and how many runs each would leave.

    A key's counts ignore that key's own filter, so a chosen procedure still
    shows how many runs the others have. A key whose values are all numbers
    says so, with its range, so the page can offer a slider.
    """
    lab = _lab(db)
    if lab is None:
        return {"runs": 0, "facets": []}
    try:
        frame = lab.facets(filters)
        total = len(lab.find(filters))
    except ValueError as e:
        raise DataRequestError(str(e)) from e
    # The units a run's metadata gives its quantities ({value, unit}); the
    # latest run's unit wins if two runs disagree.
    units: dict[str, str] = {}
    for (text,) in lab.query("SELECT metadata FROM runs WHERE metadata IS NOT NULL ORDER BY id"):
        units.update(metadata_units(json.loads(text or "{}")))
    out = []
    for (key,), part in frame.group_by("key", maintain_order=True):
        order, group = _group(str(key))
        numeric = part["num"].null_count() == 0
        entry: dict[str, Any] = {
            "key": key,
            "group": group,
            "values": [{"value": v, "runs": n} for v, n in zip(part["value"], part["runs"])],
            "numeric": numeric,
            "unit": units.get(str(key)),
        }
        if numeric:
            entry["range"] = [part["num"].min(), part["num"].max()]
        out.append((order, str(key), entry))
    return {"runs": total, "facets": [entry for _order, _key, entry in sorted(out, key=lambda e: e[:2])]}


def run_list(db: Path, filters: Mapping[str, Any], *, page: int = 1, page_size: int = 100) -> dict[str, Any]:
    """The runs matching ``filters``, newest first, one page of them."""
    page, page_size = max(1, page), max(1, min(page_size, 1000))
    lab = _lab(db)
    if lab is None:
        return {"total": 0, "page": page, "page_size": page_size, "runs": []}
    try:
        runs = lab.find(filters)
    except ValueError as e:
        raise DataRequestError(str(e)) from e
    shown = runs.ids[(page - 1) * page_size : page * page_size]
    return {
        "total": len(runs),
        "page": page,
        "page_size": page_size,
        "runs": lab.runs(shown).table().to_dicts(),
    }


# --------------------------- one run ---------------------------


def _current_definition(config_dir: str | Path, procedure: str) -> dict[str, Any] | None:
    try:
        return load_procedure(config_dir, procedure).model_dump(mode="json")
    except ValueError:  # deleted, or no longer valid
        return None


def run_detail(db: Path, config_dir: str | Path, run_id: int) -> dict[str, Any]:
    """Everything about one run, with the plots and derived columns to show it by."""
    lab = Lab(db)
    runs = lab.runs([run_id])
    info = runs.info(run_id)
    (summary,) = runs.table().to_dicts()

    current = _current_definition(config_dir, info["procedure"])
    definition = current if current is not None else info["definition"]
    columns = info["columns"] or {}
    plots = run_plots(definition, list(columns))
    return {
        "run": {**summary, "metadata": info["metadata"] or {}},
        "params": info["params"] or {},
        "instruments": info["instruments"] or {},
        "columns": columns,
        "derived": (definition or {}).get("derived") or {},
        "plots": [p.model_copy(update={"runs": [run_id]}).model_dump(mode="json") for p in plots],
        # "procedure": today's definition; "recorded": the one the run was made
        # with, because the procedure is gone; None: the run recorded none.
        "definition_source": "procedure" if current is not None else ("recorded" if definition else None),
    }


def run_steps(db: Path, run_id: int) -> list[dict[str, Any]]:
    """Every step execution of a run, in the order they started: its timeline."""
    return Lab(db).runs([run_id]).steps().drop("run_id").to_dicts()


def point_detail(db: Path, run_id: int, seq: int) -> dict[str, Any]:
    """One point: what it recorded, and the steps that recorded it."""
    lab = Lab(db)
    lab.runs([run_id])
    rows = lab.query('SELECT seq, t, steps, "values" FROM points WHERE run_id = ? AND seq = ?', (run_id, seq))
    if not rows:
        raise KeyError(f"Run {run_id} has no point {seq}")
    seq, t, steps, values = rows[0]
    return {"seq": seq, "t": t, "steps": json.loads(steps), "values": json.loads(values)}


# --------------------------- plots ---------------------------


def _spec(data: Mapping[str, Any]) -> PlotSpec:
    try:
        spec = PlotSpec.model_validate(data)
    except ValueError as e:
        raise DataRequestError(str(e)) from e
    if not spec.runs:
        raise DataRequestError("choose at least one run to plot")
    return spec


def _derived_for(lab: Lab, config_dir: str | Path, run_ids: list[int]) -> dict[str, str]:
    """The derived columns of these runs' procedures, as they are now; first run's first."""
    out: dict[str, str] = {}
    current: dict[str, dict[str, Any] | None] = {}
    runs = lab.runs(run_ids)
    for run_id, procedure in zip(runs.table()["id"], runs.table()["procedure"]):
        if procedure not in current:
            current[procedure] = _current_definition(config_dir, procedure)
        definition = current[procedure]
        if definition is None:
            definition = runs.info(run_id)["definition"]
        for name, expression in ((definition or {}).get("derived") or {}).items():
            out.setdefault(name, expression)
    return out


def plot(db: Path, config_dir: str | Path, data: Mapping[str, Any]) -> dict[str, Any]:
    """The series ``data`` (a plot spec) draws, the units of its columns, and the lines' shape."""
    from lab_wizard.lib.data.expressions import ExpressionError

    spec = _spec(data)
    lab = Lab(db)
    derived = _derived_for(lab, config_dir, spec.runs)
    try:
        rows = load_plot(spec, db, derived=derived)
    except ExpressionError as e:
        raise DataRequestError(str(e)) from e
    runs = lab.runs(spec.runs)
    units = {name: meta.get("unit") for name, meta in runs.columns().items() if isinstance(meta, dict)}
    # How the rows fall into lines, so the page can say what it drew.
    return {"series": to_series(rows), "units": units, "shape": line_shape(rows)}


def notebook(db: Path, config_dir: str | Path, data: Mapping[str, Any]) -> dict[str, Any]:
    """Python that draws ``data`` in a notebook, exactly as the page does.

    The procedures' current derived columns go into the spec itself, so the
    notebook does not depend on the procedure staying as it is.
    """
    spec = _spec(data)
    derived = {**_derived_for(Lab(db), config_dir, spec.runs), **spec.derived}
    return {"source": notebook_source(spec.model_copy(update={"derived": derived}), db)}


def save_plot_to_procedure(
    config_dir: str | Path, procedure: str, data: Mapping[str, Any], *, replace: str | None = None
) -> dict[str, Any]:
    """Add a plot to a procedure, or replace the one named ``replace`` (default: its own name).

    Saving to a built-in writes this workspace's own copy of it, as any edit of
    a built-in does.
    """
    from lab_wizard.lib.procedures.spec import ProcedureError

    definition = load_procedure(config_dir, procedure)
    try:
        spec = PlotSpec.model_validate({**data, "runs": []})
    except ValueError as e:
        raise DataRequestError(str(e)) from e
    if not spec.name:
        raise DataRequestError("a plot saved to a procedure needs a name")
    plots = list(definition.plots)
    target = replace or spec.name
    index = next((i for i, p in enumerate(plots) if p.name == target), None)
    if index is None:
        plots.append(spec)
    else:
        plots[index] = spec
    updated = definition.model_copy(update={"plots": plots})
    try:
        save_procedure(config_dir, updated)
    except ProcedureError as e:
        raise DataRequestError("; ".join(e.problems)) from e
    return {
        "plots": [p.model_dump(mode="json") for p in updated.plots],
        "origin": procedure_origin(config_dir, procedure),
    }


# --------------------------- taking a run elsewhere ---------------------------


def export_zip(db: Path, run_id: int) -> tuple[bytes, str]:
    """A run as a zipped folder of plain files (the file saver's layout), and its name."""
    Lab(db).runs([run_id])
    with tempfile.TemporaryDirectory() as tmp:
        folder = export_run(db, run_id, tmp, template="run{run_id}_{procedure}_{device}")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(folder.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(folder.parent))
        return buffer.getvalue(), f"{folder.name}.zip"


# --------------------------- file saving ---------------------------

# A made-up run, to show what a folder template gives before any run exists.
_EXAMPLE_FACETS = {
    "date": "2026-09-22",
    "time": "143012",
    "procedure": "mcr_curve",
    "device": "A7",
    "device.wafer": "W12",
    "operator": "andrew",
    "run_id": "41",
}


def _recorded_keys(db: Path) -> list[str]:
    """Keys in the open families (``device.wafer``, ``run.cryostat``, ...) some run has."""
    lab = _lab(db)
    if lab is None:
        return []
    families = " OR ".join("key LIKE ?" for _ in FOLDER_KEY_FAMILIES)
    rows = lab.query(
        f"SELECT DISTINCT key FROM run_facets WHERE {families} ORDER BY key",
        [f"{family}%" for family in FOLDER_KEY_FAMILIES],
    )
    return [row[0] for row in rows]


def _example_facets(db: Path) -> dict[str, str]:
    """The most recent run's facets, as a folder template sees them, or a made-up run."""
    lab = _lab(db)
    latest = lab.query("SELECT id, started_at FROM runs ORDER BY id DESC LIMIT 1") if lab else []
    if not latest:
        return dict(_EXAMPLE_FACETS)
    run_id, started_at = latest[0]
    facets: dict[str, str] = {}
    for key, value in lab.query("SELECT key, value FROM run_facets WHERE run_id = ? ORDER BY rowid", (run_id,)):
        facets.setdefault(key, value)
    facets["run_id"] = str(run_id)
    if started_at:
        facets["time"] = datetime.fromisoformat(started_at).astimezone().strftime("%H%M%S")
    return facets


def check_file_template(data_dir: Path, template: str) -> dict[str, Any]:
    """What ``template`` would name the latest run's folder, and what is wrong with it."""
    db = data_dir / DATABASE_NAME
    return {
        "example": str(folder_name(template, _example_facets(db))),
        "problems": check_template(template, set(_recorded_keys(db))),
    }


def file_settings(config_dir: str | Path, workspace_root: Path, data_dir: Path) -> dict[str, Any]:
    """How runs are saved as files: the settings, where they go, and the keys a template can use."""
    files = load_data_settings(config_dir).files
    root = Path(files.root).expanduser() if files.root else data_dir / "files"
    if not root.is_absolute():
        root = workspace_root / root
    return {
        "files": files.model_dump(mode="json"),
        "folder": str(root),
        "keys": [*FOLDER_KEYS, *_recorded_keys(data_dir / DATABASE_NAME)],
        **check_file_template(data_dir, files.path),
    }


def save_file_settings(
    config_dir: str | Path, workspace_root: Path, data_dir: Path, files: Mapping[str, Any]
) -> dict[str, Any]:
    """Replace the workspace's file-saving settings; every project's next run uses them."""
    settings = load_data_settings(config_dir)
    try:
        settings.files = FileSettings.model_validate(files)
    except ValueError as e:
        raise DataRequestError(str(e)) from e
    errors = [p["message"] for p in check_template(settings.files.path) if p["level"] == "error"]
    if errors:
        raise DataRequestError(" ".join(errors))
    save_data_settings(config_dir, settings)
    return file_settings(config_dir, workspace_root, data_dir)


# --------------------------- devices ---------------------------


def device_list(db: Path) -> list[dict[str, Any]]:
    """Every device the lab has recorded or registered, with its run count."""
    lab = _lab(db)
    if lab is None:
        return []
    rows = lab.query(
        """SELECT d.name, d.properties, d.notes, COUNT(r.id) FROM devices d
           LEFT JOIN runs r ON r.device_id = d.id GROUP BY d.id ORDER BY d.name"""
    )
    return [
        {"name": name, "properties": json.loads(properties or "{}"), "notes": notes, "runs": count}
        for name, properties, notes, count in rows
    ]


def save_device(db: Path, name: str, properties: Mapping[str, Any], notes: str | None = None) -> dict[str, Any]:
    """Create or update a device, and refilter every run recorded on it.

    Properties are filters (``device.wafer``), so each is a name and a single
    value; nested objects would make keys nobody could tell apart from a
    property with a dot in its name.
    """
    name = name.strip()
    if not name:
        raise DataRequestError("a device needs a name")
    for key, value in properties.items():
        if not key or "." in key:
            raise DataRequestError(f"property {key!r}: a name without dots, like wafer")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise DataRequestError(f"property {key!r}: one value (text, a number, or true/false)")
    connection = open_database(db)
    try:
        with connection:
            connection.execute(
                """INSERT INTO devices (name, properties, notes) VALUES (?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET properties = excluded.properties, notes = excluded.notes""",
                (name, json.dumps(dict(properties)), notes),
            )
            run_ids = [
                row[0]
                for row in connection.execute(
                    "SELECT r.id FROM runs r JOIN devices d ON d.id = r.device_id WHERE d.name = ?", (name,)
                )
            ]
            for run_id in run_ids:
                write_run_facets(connection, run_id)
    finally:
        connection.close()
    return {"name": name, "properties": dict(properties), "notes": notes, "runs": len(run_ids)}
