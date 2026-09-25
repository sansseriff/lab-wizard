"""Plot specs, and the one evaluator that turns a spec and points into series.

The Data page, a live plotter and a notebook all draw through
:func:`evaluate_plot`, so a plot looks the same while a run is going, after it
has been recorded, and in the notebook it was exported to
(``plans/semantic_data_plan.md`` §11).

A spec::

    runs: [41, 42]
    x: bias_voltage
    y: [count_rate]
    where: {phase: signal, trigger_mV: {per_run: min}}
    series: run          # one line per run; or a column name; or null
    label: device        # legend text for a run, from one of its filters

The result is a frame with one row per plotted point: ``run_id``, ``seq``,
``series`` (legend text), ``axis`` (``y`` or ``y2``), ``y_name``, ``x``, ``y``,
and ``z`` for waterfalls. Every expression may be a derived one (see
``expressions``).
"""

from __future__ import annotations

import pprint
from pathlib import Path
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field

from lab_wizard.lib.data.expressions import ExpressionError, Params, compile_expression, derive

__all__ = ["PlotSpec", "default_plot", "evaluate_plot", "load_plot", "notebook_source", "to_series"]

_PER_RUN = {"min", "max", "first", "last"}


class PlotSpec(BaseModel):
    """What to draw. Every field but ``x`` and ``y`` has a default."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    runs: list[int] = Field(default_factory=list)
    x: str
    y: list[str]
    y2: list[str] = Field(default_factory=list)
    # {column: value | [values] | {"in": [...]} | {"range": [lo, hi]} | {"per_run": min|max|first|last}}
    where: dict[str, Any] = Field(default_factory=dict)
    # Extra columns for this plot only: {name: expression}.
    derived: dict[str, str] = Field(default_factory=dict)
    # "run": one line per run; a column: one line per value of it; None: one line.
    series: str | None = "run"
    # A run's legend text: one of its filter keys ("device", "run.cryostat").
    label: str | None = None
    connect: Literal["seq", "x", "none"] = "seq"
    kind: Literal["line", "scatter", "histogram", "waterfall"] = "line"
    log_x: bool = False
    log_y: bool = False


def _where(spec: PlotSpec, columns: list[str], params: Params | None) -> pl.Expr:
    """Which rows to draw, as one condition. Evaluated over whole runs.

    A ``where`` chooses the points to draw; it does not change what a
    reduction sees. ``mean(count_rate, phase == "background")`` still finds
    the background while ``where: {phase: signal}`` draws only the signal.
    """
    keep = pl.lit(True)
    for column, wanted in spec.where.items():
        expr = compile_expression(column, columns, params)
        if isinstance(wanted, dict):
            if set(wanted) == {"in"}:
                keep &= expr.is_in(list(wanted["in"]))
            elif set(wanted) == {"range"}:
                lo, hi = wanted["range"]
                keep &= expr.is_between(lo, hi)
            elif set(wanted) == {"per_run"} and wanted["per_run"] in _PER_RUN:
                # "Each run's own lowest trigger level": decided per run, so
                # runs that used different levels are still comparable.
                keep &= expr == getattr(expr.drop_nulls(), wanted["per_run"])().over("run_id")
            else:
                raise ExpressionError(
                    f"where {column!r}: use a value, a list, {{'in': [...]}}, {{'range': [lo, hi]}} "
                    f"or {{'per_run': min|max|first|last}}, not {wanted!r}"
                )
        elif isinstance(wanted, list):
            keep &= expr.is_in(wanted)
        else:
            keep &= expr == wanted
    return keep.fill_null(False)


_BINDING_STEPS = {"sweep": "parameter", "repeat": "parameter", "with_parameter": "parameter"}


def _bindings(step: Any, depth: int = 0) -> list[tuple[int, str, str]]:
    """``(depth, step type, name)`` for every parameter a definition's steps bind."""
    out: list[tuple[int, str, str]] = []
    if isinstance(step, dict):
        name_field = _BINDING_STEPS.get(step.get("type", ""))
        if name_field and isinstance(step.get(name_field), str):
            out.append((depth, step["type"], step[name_field]))
        for value in step.values():
            out.extend(_bindings(value, depth + 1))
    elif isinstance(step, list):
        for item in step:
            out.extend(_bindings(item, depth))
    return out


def default_plot(definition: dict[str, Any] | None, columns: list[str]) -> PlotSpec | None:
    """What a run is drawn as when nobody chose: the procedure's first plot, or else
    its first recorded column against its innermost sweep.

    ``definition`` is a run's recorded definition (``None`` for a run without
    one); ``columns`` are the run's columns, in order.
    """
    plots = (definition or {}).get("plots") or []
    if plots:
        return PlotSpec.model_validate(plots[0])
    bound = _bindings((definition or {}).get("body"))
    sweeps = [(depth, name) for depth, kind, name in bound if kind == "sweep" and name in columns]
    bound_names = {name for _depth, _kind, name in bound}
    recorded = [c for c in columns if c not in bound_names]
    x = max(sweeps)[1] if sweeps else (columns[0] if columns else None)
    y = next((c for c in recorded if c != x), None)
    if x is None or y is None:
        return None
    return PlotSpec(x=x, y=[y], series=None)


def _labels(spec: PlotSpec, run_labels: dict[int, str] | None) -> pl.Expr:
    run_text = pl.format("run {}", pl.col("run_id"))
    if run_labels:
        run_text = pl.col("run_id").replace_strict(run_labels, default=None, return_dtype=pl.String).fill_null(run_text)
    if spec.series == "run":
        return run_text
    if spec.series:
        return pl.format("{} = {}", pl.lit(spec.series), pl.col(spec.series).cast(pl.String))
    return pl.lit("")


def evaluate_plot(
    spec: PlotSpec | dict[str, Any],
    points: pl.DataFrame,
    *,
    run_labels: dict[int, str] | None = None,
    bins: dict[str, dict[str, Any]] | None = None,
    params: Params | None = None,
    derived: dict[str, str] | None = None,
) -> pl.DataFrame:
    """The rows to draw for ``spec``, from ``points`` (as ``Runs.points`` gives).

    ``run_labels`` maps a run id to its legend text; ``bins`` gives an array
    column's bin axis, ``{name: {"start": 0, "step": 4}}``, as recorded in
    ``runs.columns``. ``params`` is ``{run_id: params}``, for ``param("...")``.
    ``derived`` adds the procedure's own derived columns; the spec's
    ``derived`` are added over them.
    """
    spec = spec if isinstance(spec, PlotSpec) else PlotSpec.model_validate(spec)
    frame = points.sort(["run_id", "seq"]) if {"run_id", "seq"} <= set(points.columns) else points
    all_derived = {**(derived or {}), **spec.derived}
    if all_derived:
        frame = derive(frame, all_derived, params)

    keep = _where(spec, frame.columns, params)
    series = _labels(spec, run_labels)
    x = compile_expression(spec.x, frame.columns, params)
    parts: list[pl.DataFrame] = []
    for axis, names in (("y", spec.y), ("y2", spec.y2)):
        for name in names:
            y = compile_expression(name, frame.columns, params)
            label = series if len(spec.y) + len(spec.y2) == 1 else (
                pl.concat_str([series, pl.lit(name)], separator=" · ") if spec.series else pl.lit(name)
            )
            # Everything is computed over the whole run first; only then are
            # the rows to draw picked out.
            part = frame.select(
                pl.col("run_id"), pl.col("seq"), label.alias("series"), pl.lit(axis).alias("axis"),
                pl.lit(name).alias("y_name"), x.alias("x"), y.alias("y"), keep.alias("keep"),
            ).filter(pl.col("keep")).drop("keep")
            parts.append(_shape(spec, part, name, (bins or {}).get(name)))
    if not parts:
        raise ExpressionError("a plot needs at least one y")
    out = pl.concat(parts, how="diagonal_relaxed")
    if "z" not in out.columns:
        out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias("z"))
    if spec.connect == "x" and spec.kind in ("line", "scatter"):
        out = out.sort(["axis", "y_name", "series", "x"], maintain_order=True)
    return out.select("run_id", "seq", "series", "axis", "y_name", "x", "y", "z")


def _shape(spec: PlotSpec, part: pl.DataFrame, name: str, bins: dict[str, Any] | None) -> pl.DataFrame:
    """Line and scatter plots as they are; array columns spread over their bins."""
    if spec.kind in ("line", "scatter"):
        return part.drop_nulls(["x", "y"])
    if not isinstance(part.schema["y"], pl.List):
        raise ExpressionError(f"a {spec.kind} plots an array column, and {name!r} is not one")
    start = (bins or {}).get("start", 0)
    step = (bins or {}).get("step", 1)
    spread = (
        part.drop_nulls("y")
        .with_columns(pl.int_ranges(0, pl.col("y").list.len()).alias("bin"))
        .explode(["y", "bin"], empty_as_null=False)  # an empty array draws nothing
        .with_columns((pl.lit(start) + pl.col("bin") * step).cast(pl.Float64).alias("bin"))
    )
    if spec.kind == "histogram":
        # One histogram per point: the bins across, the contents up.
        return spread.with_columns(
            pl.format("{} · seq {}", pl.col("series"), pl.col("seq")).alias("series"),
            pl.col("bin").alias("x"),
        ).drop("bin")
    # A waterfall: bins across, the spec's x up, the contents as colour.
    return spread.with_columns(pl.col("x").alias("y_row"), pl.col("y").alias("z")).select(
        "run_id", "seq", "series", "axis", "y_name", pl.col("bin").alias("x"), pl.col("y_row").alias("y"), "z"
    )


def to_series(rows: pl.DataFrame) -> list[dict[str, Any]]:
    """``evaluate_plot`` rows grouped into drawable series.

    Each is ``{label, axis, y_name, x, y, z, run_id, seq}``, the last two naming
    the point each value came from, so a click on a point can find its steps.
    """
    out = []
    for (label, axis, y_name), part in rows.group_by(["series", "axis", "y_name"], maintain_order=True):
        out.append({
            "label": label, "axis": axis, "y_name": y_name,
            "x": part["x"].to_list(), "y": part["y"].to_list(), "z": part["z"].to_list(),
            "run_id": part["run_id"].to_list(), "seq": part["seq"].to_list(),
        })
    return out


def load_plot(
    spec: PlotSpec | dict[str, Any],
    db: str | Path | None = None,
    *,
    derived: dict[str, str] | None = None,
) -> pl.DataFrame:
    """Evaluate ``spec`` against its runs in the lab database.

    ``derived`` are the procedure's derived columns; by default, the ones each
    run recorded with its definition. The Data page passes the procedure's
    current ones, so a derived column added later applies to past runs.
    """
    from lab_wizard.lib.data.read import Lab

    spec = spec if isinstance(spec, PlotSpec) else PlotSpec.model_validate(spec)
    with Lab(db) as lab:
        runs = lab.runs(spec.runs)
        points = runs.points()
        labels: dict[int, str] = {}
        if spec.label:
            where = ", ".join("?" * len(spec.runs))
            labels = {
                run_id: value
                for run_id, value in lab.query(
                    f"SELECT run_id, value FROM run_facets WHERE key = ? AND run_id IN ({where})",
                    [spec.label, *spec.runs],
                )
            }
        bins = {name: meta["bins"] for name, meta in runs.columns().items() if isinstance(meta, dict) and meta.get("bins")}
        return evaluate_plot(
            spec, points, run_labels=labels, bins=bins, params=runs.params(),
            derived=runs.derived() if derived is None else derived,
        )


def notebook_source(spec: PlotSpec | dict[str, Any], db: str | Path) -> str:
    """Python that reproduces ``spec`` in a notebook, starting where the viewer stopped."""
    spec = spec if isinstance(spec, PlotSpec) else PlotSpec.model_validate(spec)
    data = spec.model_dump(exclude_defaults=True)
    title = spec.name or f"{', '.join(spec.y)} against {spec.x}"
    if spec.kind == "waterfall":
        draw = (
            "for series, part in df.group_by(\"series\", maintain_order=True):\n"
            "    ax.scatter(part[\"x\"], part[\"y\"], c=part[\"z\"], marker=\"s\")\n"
        )
    else:
        style = "'o'" if spec.kind == "scatter" or spec.connect == "none" else "'-o'"
        draw = (
            "axes = {\"y\": ax}\n"
            + ("axes[\"y2\"] = ax.twinx()\n" if spec.y2 else "")
            + "for (series, axis, y_name), part in df.group_by([\"series\", \"axis\", \"y_name\"], maintain_order=True):\n"
            + f"    axes[axis].plot(part[\"x\"], part[\"y\"], {style}, label=series or y_name)\n"
        )
    scales = "".join(f"ax.set_{axis}scale(\"log\")\n" for axis, on in (("x", spec.log_x), ("y", spec.log_y)) if on)
    return (
        "from lab_wizard.lib.data import load_plot\n"
        "import matplotlib.pyplot as plt\n\n"
        f"spec = {pprint.pformat(data, width=88, sort_dicts=False)}\n"
        f"df = load_plot(spec, db={str(db)!r})\n\n"
        "fig, ax = plt.subplots()\n"
        f"{draw}"
        f"ax.set_xlabel({spec.x!r})\n"
        f"ax.set_ylabel({', '.join(spec.y)!r})\n"
        f"ax.set_title({title!r})\n"
        f"{scales}"
        + ("" if spec.kind == "waterfall" else "fig.legend()\n")
        + "plt.show()\n"
    )
