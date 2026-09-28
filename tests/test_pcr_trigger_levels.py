"""pcr_trigger_levels: two nested sweeps, and how the plot is chosen from them.

The procedure sweeps the bias and, at each bias, the counter's trigger level.
Every (bias, trigger level) pair is one row holding both values and the count,
whichever loop is outside (the point rule). What a plot of those rows should
look like, bias across with a line per trigger level, or trigger across with a
line per bias, is not in the rows; the procedure's own plot says it. These
tests run the procedure against the simulated bench and check both halves:
the rows do not depend on the loop order, and the declared plot draws one
line per trigger level.
"""

from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import polars as pl
import pytest
from ruamel.yaml import YAML

from lab_wizard.lib.data import find, load_plot, to_series
from lab_wizard.lib.data.plot import default_plot
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.storage import load_procedure, save_procedure
from lab_wizard.lib.workspace import initialize_workspace
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest

TRIGGERS = [-25.0, -90.0, -110.0, -130.0, -150.0, -170.0]
BIASES = [round(0.0015 * i, 4) for i in range(20)]  # 0 .. 0.0285 V, below switching


def _swapped(definition: ProcedureDefinition) -> ProcedureDefinition:
    """The same procedure with the trigger sweep outside and the bias sweep inside."""
    data = deepcopy(definition.model_dump(mode="json", exclude_none=True))
    bias_sweep = data["body"]["body"]
    set_voltage, wait, trigger_sweep = bias_sweep["body"]["children"]
    set_threshold, count = trigger_sweep["body"]["children"]
    data["name"] = "pcr_trigger_levels_swapped"
    data["body"]["body"] = {
        **trigger_sweep,
        "body": {
            "type": "sequence",
            "children": [set_threshold, {**bias_sweep, "body": {"type": "sequence", "children": [set_voltage, wait, count]}}],
        },
    }
    return ProcedureDefinition.model_validate(data)


def _run(workspace: Any, rig, procedure: str) -> None:
    out = generate_procedure_project(
        config_dir=workspace.config_dir,
        projects_dir=workspace.projects_dir,
        req=GenerateProjectRequest(
            measurement_name=procedure,
            kind="procedure",
            selected_resources=[rig.select("voltage_source", "source"), rig.select("counter", "counter")],
            project_prefix=procedure,
        ),
    )
    path = Path(out["yaml_file"])
    yaml = YAML(typ="rt")
    data = yaml.load(path.read_text())
    params = data["measurement"]["params"]
    params["bias"]["settle_s"] = 0.0
    params["readout"]["gate_time_s"] = 0.02
    with path.open("w") as f:
        yaml.dump(data, f)
    setup = Path(out["setup_file"])
    result = subprocess.run([sys.executable, str(setup)], cwd=setup.parent, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr


@pytest.fixture
def lab(tmp_path: Path, rig):
    """One run of each loop order, recorded into one workspace's lab database."""
    workspace, _ = initialize_workspace(tmp_path / "workspace")
    rig.write(workspace.config_dir, ("source", "counter"))
    save_procedure(workspace.config_dir, _swapped(load_procedure(workspace.config_dir, "pcr_trigger_levels")))
    _run(workspace, rig, "pcr_trigger_levels")
    _run(workspace, rig, "pcr_trigger_levels_swapped")
    return workspace.data_dir / "lab.db"


def test_it_ships_built_in_and_declares_both_sweeps_as_columns(tmp_path: Path):
    definition = load_procedure(tmp_path / "config", "pcr_trigger_levels")
    definition.check()
    assert definition.warnings() == []
    columns = definition.columns()
    assert (columns["bias_voltage"]["unit"], columns["trigger_mV"]["unit"]) == ("V", "mV")
    assert {"count_rate", "trigger_mV_reached"} <= set(columns)


def test_every_bias_and_trigger_pair_is_one_row_whichever_loop_is_outside(lab):
    frames = {name: find(db=lab, procedure=name).points() for name in ("pcr_trigger_levels", "pcr_trigger_levels_swapped")}
    counted = {name: f.filter(pl.col("count_rate").is_not_null()) for name, f in frames.items()}

    expected = sorted((b, t) for b in BIASES for t in TRIGGERS)
    for name, f in counted.items():
        pairs = sorted(zip(f["bias_voltage"].round(6), f["trigger_mV"]))
        assert pairs == expected, name
    # Only the order they were recorded in differs.
    assert counted["pcr_trigger_levels"]["trigger_mV"][: len(TRIGGERS)].to_list() == TRIGGERS  # triggers vary fastest
    assert counted["pcr_trigger_levels_swapped"]["bias_voltage"][:3].round(6).to_list() == BIASES[:3]


def test_a_reading_that_moves_to_the_outer_loop_gets_rows_of_its_own(lab):
    """What loop order does change: where a step runs.

    Swapping the loops moves setting the trigger level outside the bias sweep,
    so the level it reached is read once per trigger level, not once per
    point: a row of its own, with no bias. This is plans/semantic_data_plan.md
    §14's fill-down case, and a plot of count against bias simply skips it.
    """
    inner = find(db=lab, procedure="pcr_trigger_levels").points()
    outer = find(db=lab, procedure="pcr_trigger_levels_swapped").points()
    assert inner["trigger_mV_reached"].null_count() == 0  # beside every count
    alone = outer.filter(pl.col("trigger_mV_reached").is_not_null())
    assert alone["trigger_mV"].to_list() == TRIGGERS
    assert alone["bias_voltage"].null_count() == len(TRIGGERS)
    assert alone["count_rate"].null_count() == len(TRIGGERS)
    assert outer.height == inner.height + len(TRIGGERS)


def test_the_declared_plot_draws_one_line_per_trigger_level_for_either_loop_order(lab):
    for procedure in ("pcr_trigger_levels", "pcr_trigger_levels_swapped"):
        runs = find(db=lab, procedure=procedure)
        (run_id,) = runs.ids
        spec = ProcedureDefinition.model_validate(runs.info(run_id)["definition"]).plots[0]
        assert (spec.x, spec.series) == ("bias_voltage", "trigger_mV")

        series = to_series(load_plot(spec.model_copy(update={"runs": [run_id]}), lab))
        assert [s["label"] for s in series] == [f"trigger_mV = {t}" for t in TRIGGERS], procedure
        assert all(s["x"] == pytest.approx(BIASES) for s in series), procedure


def test_higher_triggers_turn_on_later_and_the_lowest_counts_a_noise_floor(lab):
    """What a real multi-trigger PCR plot shows, and why the plot exists."""
    (run_id,) = find(db=lab, procedure="pcr_trigger_levels").ids
    spec = load_procedure(Path("unused"), "pcr_trigger_levels").plots[0].model_copy(update={"runs": [run_id]})
    rates = {
        s["label"]: dict(zip((round(x, 6) for x in s["x"]), s["y"])) for s in to_series(load_plot(spec, lab))
    }
    at = {trigger: rates[f"trigger_mV = {trigger}"] for trigger in TRIGGERS}
    # The lowest trigger reaches the readout's noise: counts with no bias at all.
    assert at[-25.0][0.0] > 30_000
    assert all(at[t][0.0] < 200 for t in TRIGGERS[1:])
    # Pulses grow with the bias, so each higher trigger turns on later.
    mid = [at[t][0.021] for t in TRIGGERS[1:]]  # 140 mV pulses
    assert mid == sorted(mid, reverse=True)
    assert mid[-1] < mid[0] / 3
    # Near switching the pulses clear every trigger but the highest nearly all the time.
    assert at[-170.0][0.0285] > 0.6 * at[-90.0][0.0285]


def test_the_second_plot_reads_counts_against_trigger_level_at_the_top_bias(lab):
    (run_id,) = find(db=lab, procedure="pcr_trigger_levels").ids
    spec = load_procedure(Path("unused"), "pcr_trigger_levels").plots[1].model_copy(update={"runs": [run_id]})
    rows = load_plot(spec, lab)
    assert rows["x"].to_list() == TRIGGERS  # the three rows at the run's highest bias
    points = find(db=lab, procedure="pcr_trigger_levels").points()
    top = points.filter(pl.col("bias_voltage") == points["bias_voltage"].max())
    assert rows["y"].to_list() == top["count_rate"].to_list()


def test_without_its_plots_the_guess_would_draw_a_line_per_bias():
    """The fallback cannot know what the procedure means; this is why it declares."""
    definition = load_procedure(Path("unused"), "pcr_trigger_levels").model_dump(mode="json")
    definition["plots"] = []
    guess = default_plot(definition, ["bias_voltage", "trigger_mV", "trigger_mV_reached", "counts", "count_rate"])
    assert (guess.x, guess.series) == ("trigger_mV", "bias_voltage")
