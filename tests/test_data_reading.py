"""Reading the lab database back: filters, points, derived columns, plots.

The fixture records three real runs through the recorder: two PCR runs on
different devices that used *different* trigger levels, and an MCR run with a
background count. That is enough to check the cases the Data page is for,
including "the lowest trigger level of each of these runs".
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import matplotlib
import polars as pl
import pytest

from lab_procedure import ProcedureRunner, RunStarted, Sequence, Status, Step, Sweep, WithParameter
from lab_wizard.lib.data import (
    DatabaseRecorder,
    ExpressionError,
    Lab,
    PlotSpec,
    compile_expression,
    derive,
    evaluate_plot,
    find,
    lab_database,
    load_plot,
    notebook_source,
    to_series,
)
from lab_wizard.lib.data.facets import write_run_facets
from lab_wizard.lib.workspace import WORKSPACE_ENV, initialize_workspace

matplotlib.use("Agg")


class Measure(Step):
    """Records ``fields``, each computed from the parameters in force."""

    def __init__(self, **fields: Any) -> None:
        super().__init__()
        self.fields = fields

    def run(self) -> Status:
        assert self.context is not None
        p = self.context.parameters
        self.context.observe({k: f(p) if callable(f) else f for k, f in self.fields.items()})
        return Status.SUCCESS


def rate(p: dict) -> float:
    return round(1e5 * p["bias_voltage"] * (1 + p["trigger_mV"] / 100), 1)


def _record(db: Path, tree: Step, started: RunStarted) -> None:
    recorder = DatabaseRecorder(db)
    runner = ProcedureRunner()
    recorder.attach(runner.context.data_bus, runner.context.status_bus)
    runner.run(tree, started)
    recorder.close()


def _pcr(triggers: list[int]) -> Step:
    return Sweep(
        "trigger_mV", triggers,
        lambda t: Sweep("bias_voltage", [0.02, 0.03], lambda b: Measure(count_rate=rate, device_voltage=0.0)),
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "lab.db"
    _record(path, _pcr([-50, -40]), RunStarted(
        procedure="pcr_curve", device="A7", operator="andrew",
        params={"readout": {"gate_time_s": 1.0}}, metadata={"cryostat": "BlueFors1"},
        columns={"trigger_mV": {"unit": "mV"}, "bias_voltage": {"unit": "V"}, "count_rate": {"unit": "Hz"}},
    ))
    _record(path, _pcr([-30, -20]), RunStarted(
        procedure="pcr_curve", device="B2", operator="sam",
        params={"readout": {"gate_time_s": 0.5}}, metadata={"cryostat": "BlueFors2"},
    ))
    _record(path, Sequence(
        WithParameter("phase", "background", Measure(counts=10.0)),
        WithParameter("phase", "signal", Sweep("attenuation_db", [20.0, 10.0, 0.0],
                                               lambda a: Measure(counts=lambda p: 10 + 1000 / (1 + p["attenuation_db"])))),
    ), RunStarted(procedure="mcr_curve", device="A7", operator="andrew", params={"readout": {"gate_time_s": 1.0}}))
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute("""update devices set properties = '{"type": "SNSPD-A"}' where name = 'A7'""")
        conn.execute("""update devices set properties = '{"type": "SNSPD-B"}' where name = 'B2'""")
        conn.row_factory = sqlite3.Row
        for run_id in (1, 2, 3):
            write_run_facets(conn, run_id)
    return path


# --------------------------- finding runs ---------------------------


def test_find_filters_by_any_facet_and_lists_newest_first(db: Path):
    with Lab(db) as lab:
        assert lab.find().ids == [3, 2, 1]
        assert lab.find(procedure="pcr_curve").ids == [2, 1]
        assert lab.find({"device.type": "SNSPD-A"}).ids == [3, 1]
        assert lab.find({"device.type": "SNSPD-A"}, procedure="pcr_curve").ids == [1]
        assert lab.find(device=["A7", "B2"], procedure="pcr_curve").ids == [2, 1]
        assert lab.find({"param.readout.gate_time_s": 1.0}).ids == [3, 1]  # a float matches its stored text
        assert lab.find({"param.readout.gate_time_s": {"range": [0.1, 0.7]}}).ids == [2]
        assert lab.find({"run.cryostat": "nowhere"}).ids == []


def test_facet_counts_are_the_choices_a_sidebar_offers(db: Path):
    with Lab(db) as lab:
        everything = {(r["key"], r["value"]): r["runs"] for r in lab.facets().iter_rows(named=True)}
        assert everything[("procedure", "pcr_curve")] == 2
        assert everything[("device", "A7")] == 2
        assert everything[("column", "count_rate")] == 2

        chosen = {(r["key"], r["value"]): r["runs"] for r in lab.facets({"procedure": "pcr_curve"}).iter_rows(named=True)}
        # Choosing a procedure narrows every other filter...
        assert chosen[("device", "A7")] == 1
        assert ("column", "attenuation_db") not in chosen
        # ...but still offers the other procedures, with their own counts.
        assert chosen[("procedure", "mcr_curve")] == 1
        assert chosen[("procedure", "pcr_curve")] == 2


def test_a_run_table_says_what_each_run_was(db: Path):
    table = find(db=db, procedure="pcr_curve").table()
    assert table.select("id", "device", "operator", "points").rows() == [(2, "B2", "sam", 4), (1, "A7", "andrew", 4)]


def test_asking_for_a_run_that_does_not_exist_names_it(db: Path):
    with Lab(db) as lab, pytest.raises(KeyError, match="99"):
        lab.runs([1, 99])


def test_the_current_workspaces_database_is_found(tmp_path: Path, monkeypatch):
    ws, _ = initialize_workspace(tmp_path / "ws")
    monkeypatch.setenv(WORKSPACE_ENV, str(ws.root))
    assert lab_database() == ws.data_dir / "lab.db"


# --------------------------- points ---------------------------


def test_points_are_one_row_per_point_with_nulls_where_nothing_was_recorded(db: Path):
    with Lab(db) as lab:
        points = lab.runs([3]).points()
    assert points.columns[:3] == ["run_id", "seq", "t"]
    assert points.select("phase", "attenuation_db", "counts").rows() == [
        ("background", None, 10.0),
        ("signal", 20.0, 10 + 1000 / 21),
        ("signal", 10.0, 10 + 1000 / 11),
        ("signal", 0.0, 1010.0),
    ]
    assert points.schema["t"] == pl.Datetime("us", "UTC")


def test_points_from_several_runs_share_one_frame(db: Path):
    with Lab(db) as lab:
        points = lab.runs([1, 3]).points()
    assert points["run_id"].unique().sort().to_list() == [1, 3]
    assert {"count_rate", "counts", "phase"} <= set(points.columns)
    assert points.filter(pl.col("run_id") == 3)["count_rate"].null_count() == 4


def test_the_timeline_lists_every_step(db: Path):
    with Lab(db) as lab:
        steps = lab.runs([1]).steps()
    assert steps["path"][0] == "sweep"
    assert (steps["status"] == "success").all()


# --------------------------- derived expressions ---------------------------


def _frame() -> pl.DataFrame:
    return pl.DataFrame({
        "run_id": [1, 1, 1, 2, 2],
        "seq": [0, 1, 2, 0, 1],
        "phase": ["background", "signal", "signal", "background", "signal"],
        "count_rate": [20.0, 120.0, 220.0, 50.0, 150.0],
        "int_time": [1.0, 1.0, 2.0, 1.0, 1.0],
    })


def _eval(text: str) -> list[Any]:
    frame = _frame()
    return frame.select(compile_expression(text, frame.columns).alias("v"))["v"].to_list()


def test_row_arithmetic_and_functions():
    assert _eval("count_rate / int_time") == [20.0, 120.0, 110.0, 50.0, 150.0]
    assert _eval("abs(-count_rate) + 1") == [21.0, 121.0, 221.0, 51.0, 151.0]
    assert _eval("log10(count_rate * 5)") == pytest.approx([2.0, 2.7781, 3.0414, 2.3979, 2.8751], abs=1e-4)


def test_reductions_are_per_run():
    assert _eval("count_rate / max(count_rate)") == pytest.approx([20 / 220, 120 / 220, 1.0, 50 / 150, 1.0])
    assert _eval("first(count_rate)") == [20.0, 20.0, 20.0, 50.0, 50.0]
    assert _eval("last(seq)") == [2, 2, 2, 1, 1]


def test_background_subtraction_uses_each_runs_own_background():
    assert _eval('count_rate - mean(count_rate, phase == "background")') == [0.0, 100.0, 200.0, 0.0, 100.0]
    assert _eval('count(count_rate, phase == "signal" and count_rate > 130)') == [1, 1, 1, 1, 1]


@pytest.mark.parametrize(
    ("text", "complaint"),
    [
        ("counts * 2", "no column named 'counts'"),
        ('phase == "signal"', "only allowed as a reduction's condition"),
        ("count_rate.__class__", "not part of the expression language"),
        ("__import__('os')", "unknown function '__import__'"),
        ("open('/etc/passwd')", "unknown function 'open'"),
        ("(lambda: 1)()", "unknown function"),
        ("count_rate[0]", "not part of the expression language"),
        ("max(count_rate, key=1)", "takes no keyword arguments"),
        ("mean(count_rate, int_time)", "not a condition"),
        ("sqrt(1, 2)", "takes one argument"),
        ("count_rate +", "is not an expression"),
    ],
)
def test_anything_outside_the_language_is_refused(text: str, complaint: str):
    with pytest.raises(ExpressionError, match=None) as info:
        compile_expression(text, _frame().columns)
    assert complaint in str(info.value)


def test_derived_columns_may_build_on_each_other_in_any_order():
    frame = derive(_frame(), {
        "normalized": "above_dark / max(above_dark)",
        "above_dark": 'count_rate - mean(count_rate, phase == "background")',
    })
    assert frame["normalized"].to_list() == pytest.approx([0.0, 0.5, 1.0, 0.0, 1.0])


def test_a_derived_cycle_or_a_clash_with_a_recorded_column_is_refused():
    with pytest.raises(ExpressionError, match="cycle"):
        derive(_frame(), {"a": "b + 1", "b": "a + 1"})
    with pytest.raises(ExpressionError, match="would replace a recorded column"):
        derive(_frame(), {"count_rate": "int_time"})


# --------------------------- plots ---------------------------


def test_the_lowest_trigger_level_of_each_run_on_one_plot(db: Path):
    """The case the overlay was designed around: the runs used different levels."""
    rows = load_plot({
        "runs": [1, 2], "x": "bias_voltage", "y": ["count_rate"],
        "where": {"trigger_mV": {"per_run": "min"}}, "series": "run", "label": "device",
    }, db)
    series = {s["label"]: (s["x"], s["y"]) for s in to_series(rows)}
    assert series == {
        "A7": ([0.02, 0.03], [rate({"bias_voltage": 0.02, "trigger_mV": -50}), rate({"bias_voltage": 0.03, "trigger_mV": -50})]),
        "B2": ([0.02, 0.03], [rate({"bias_voltage": 0.02, "trigger_mV": -30}), rate({"bias_voltage": 0.03, "trigger_mV": -30})]),
    }


def test_one_run_split_into_a_line_per_value_of_a_column(db: Path):
    rows = load_plot({"runs": [1], "x": "bias_voltage", "y": ["count_rate"], "series": "trigger_mV"}, db)
    assert [s["label"] for s in to_series(rows)] == ["trigger_mV = -50", "trigger_mV = -40"]


def test_a_measured_value_against_another_measured_value(db: Path):
    rows = load_plot({"runs": [1], "x": "device_voltage", "y": ["count_rate"], "series": None}, db)
    assert rows.height == 4 and set(rows["x"]) == {0.0}


def test_several_ys_and_a_second_axis(db: Path):
    rows = load_plot({
        "runs": [3], "x": "attenuation_db", "y": ["counts"], "y2": ["counts / max(counts)"],
        "where": {"phase": "signal"},
    }, db)
    by_axis = {(s["axis"], s["y_name"]): s for s in to_series(rows)}
    assert set(by_axis) == {("y", "counts"), ("y2", "counts / max(counts)")}
    assert by_axis[("y2", "counts / max(counts)")]["y"][-1] == 1.0
    assert by_axis[("y", "counts")]["label"] == "run 3 · counts"


def test_where_picks_the_points_to_draw_but_reductions_still_see_the_whole_run():
    rows = evaluate_plot(
        {"x": "seq", "y": ['count_rate - mean(count_rate, phase == "background")'], "where": {"phase": "signal"}},
        _frame(),
    )
    assert rows.select("run_id", "y").rows() == [(1, 100.0), (1, 200.0), (2, 100.0)]


def test_points_join_in_recording_order_unless_asked_to_sort_by_x():
    # A hysteresis sweep: up, then back down over the same values.
    points = pl.DataFrame({"run_id": [1] * 5, "seq": range(5), "bias": [0.0, 1.0, 2.0, 1.0, 0.0], "r": [0, 1, 4, 3, 1]})
    in_order = evaluate_plot({"x": "bias", "y": ["r"]}, points)
    assert in_order["x"].to_list() == [0.0, 1.0, 2.0, 1.0, 0.0]  # the loop stays visible
    by_x = evaluate_plot({"x": "bias", "y": ["r"], "connect": "x"}, points)
    assert by_x["x"].to_list() == [0.0, 0.0, 1.0, 1.0, 2.0]


def test_array_columns_draw_as_histograms_and_waterfalls():
    points = pl.DataFrame({
        "run_id": [1, 1], "seq": [0, 1], "bias": [0.02, 0.03], "arrival_time": [[1.0, 5.0, 2.0], [2.0, 9.0, 3.0]],
    })
    bins = {"arrival_time": {"start": 100.0, "step": 4.0}}
    histogram = evaluate_plot(
        {"x": "bias", "y": ["arrival_time"], "kind": "histogram", "where": {"seq": 1}}, points, bins=bins
    )
    assert histogram.select("x", "y").rows() == [(100.0, 2.0), (104.0, 9.0), (108.0, 3.0)]

    waterfall = evaluate_plot({"x": "bias", "y": ["arrival_time"], "kind": "waterfall"}, points, bins=bins)
    assert waterfall.height == 6
    assert waterfall.filter(pl.col("x") == 104.0).select("y", "z").rows() == [(0.02, 5.0), (0.03, 9.0)]

    with pytest.raises(ExpressionError, match="not one"):
        evaluate_plot({"x": "bias", "y": ["bias"], "kind": "histogram"}, points)


def test_an_unknown_where_form_is_explained():
    points = pl.DataFrame({"run_id": [1], "seq": [0], "a": [1.0]})
    with pytest.raises(ExpressionError, match="per_run"):
        evaluate_plot({"x": "a", "y": ["a"], "where": {"a": {"lowest": True}}}, points)


def test_a_spec_rejects_fields_it_does_not_know():
    with pytest.raises(ValueError, match="colour"):
        PlotSpec.model_validate({"x": "a", "y": ["a"], "colour": "red"})


def test_the_notebook_export_runs_and_draws_the_same_data(db: Path, monkeypatch):
    import matplotlib.pyplot as plt

    monkeypatch.setattr(plt, "show", lambda: None)
    spec = {
        "runs": [1, 2], "x": "bias_voltage", "y": ["count_rate"], "y2": ["count_rate / max(count_rate)"],
        "where": {"trigger_mV": {"per_run": "min"}}, "label": "device", "log_y": True,
    }
    source = notebook_source(spec, db)
    assert "load_plot(spec, db=" in source
    namespace: dict[str, Any] = {}
    exec(compile(source, "<notebook>", "exec"), namespace)

    assert namespace["df"].equals(load_plot(spec, db))
    lines = [line for ax in namespace["fig"].axes for line in ax.get_lines()]
    assert len(lines) == 4  # two runs, two axes
    assert namespace["ax"].get_yscale() == "log"
    plt.close("all")


def test_evaluate_plot_serves_a_live_plotter_from_rows_in_memory():
    """A live plotter has no database, only the rows so far; the same evaluator draws them."""
    rows = [{"run_id": 7, "seq": i, "bias": b, "r": b * 10} for i, b in enumerate([0.1, 0.2])]
    series = to_series(evaluate_plot({"x": "bias", "y": ["r / max(r)"], "series": None}, pl.DataFrame(rows)))
    assert series == [{"label": "", "axis": "y", "y_name": "r / max(r)", "x": [0.1, 0.2], "y": [0.5, 1.0], "z": [None, None]}]
    json.dumps(series)  # ready to send to a browser
