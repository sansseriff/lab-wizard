---
icon: lucide/database
---

# The lab database

Every run started from a project is recorded in one SQLite file per workspace,
**`data/lab.db`**. It needs no configuring and no saver: a project that runs is
recorded. A project outside any workspace records into its own `data/lab.db`.
Code: [`lib/data/`](../../lab_wizard/lib/data/).

## What a run records

- **The run**: its procedure, status (`running`, `success`, `failed`,
  `aborted`, or `interrupted` if its process died without ending it — killed,
  crashed, or a power cut), start and end times, the operator and notes, and
  the setup it was taken on: a copy of the setup's fields and its mounted
  device (the device under test) as they were when the run started, and which
  field filled each of its procedure's needs.
- **One row per point.** A row is everything recorded while the same parameter
  values were in force (see
  [How readings become rows](../concepts/procedures.md#how-readings-become-rows)),
  so an MCR point's count and device voltage share a row, and the order of a
  procedure's loops does not change the rows.
- **Every step it executed**, with start and end times, status, and the error
  that ended it, if any: the run's timeline.
- **The params it ran with, and what each instrument was configured with**,
  keyed by role, because the config those came from is edited between runs.
- **The procedure definition it ran**, including its plots and derived columns,
  so a run explains itself after the procedure is edited or deleted.
- **Its columns, with units**, from the procedure's sweeps and steps.

## The tables

| Table | One row per | Holds |
|---|---|---|
| `setups` | setup | `name`, `notes`, `device_id` (what is mounted), and JSON `fields` (its current facts) |
| `runs` | run | `procedure`, `status`, `started_at`, `ended_at`, `device_id`, `operator`, `notes`, `project`, `setup`, and JSON `setup_fields`, `setup_needs`, `definition`, `params`, `instruments`, `columns` |
| `points` | point of a run | `run_id`, `seq` (recording order), `t`, the `steps` that recorded it, and JSON `values`: the parameters in force and every reading |
| `steps` | step execution | `run_id`, `path`, `kind`, `started_at`, `ended_at`, `status`, `error` |
| `devices` | device | `name`, and JSON `properties` (type, wafer, width …) that apply to every run on it |
| `run_facets` | filter a run matches | `key`, `value`, `num`: derived from the rest, for the Data page's sidebar |
| `meta` | setting | the schema version |

**A procedure adds no columns.** Everything it records is a key in a point's
`values`, so the schema never changes when a procedure is written, and a column
cannot outlive the runs that recorded it.

`run_facets` flattens each run's facts into filters when it ends: `procedure`,
`device`, `device.<property>`, `operator`, `date`, `setup`, `setup.<field>`,
`instrument.<role>.type`, `instrument.<role>.<param>`, `param.<path>`, and
`column` for each column it recorded. A filter finds only what was recorded, so
mount the device in the setup before a run.

## Reading runs back

Runs in the lab database are read with
[`lab_wizard.lib.data`](../../lab_wizard/lib/data/), which returns
[polars](https://pola.rs) frames. No SQL is involved:

```python
from lab_wizard.lib.data import find, facets, load_plot

runs = find(procedure="mcr_curve", device="A7")          # newest first
runs = find({"device.type": "SNSPD-A", "setup.cryostat": "BlueFors1"})
runs = find({"param.readout.gate_time_s": {"range": [0.1, 1.0]}})

runs.table()     # one row per run: date, procedure, device, operator, points
runs.points()    # one row per point: run_id, seq, t, then every column
runs.steps()     # every step each run executed: the timeline
facets()         # every filter there is, with how many runs each leaves
```

`find` and `facets` use the workspace you are in (or `LAB_WIZARD_WORKSPACE`);
pass `db=` to name a `lab.db` directly. A filter is any facet: `procedure`,
`device`, `device.<property>`, `operator`, `date`, `setup`, `setup.<field>`,
`instrument.<role>.type`, `instrument.<role>.<param>`, `param.<path>`, or
`column` (runs that recorded a column).

**Derived columns** are expressions computed when read, never stored:

```python
runs.points(derived={
    "above_dark": 'count_rate - mean(count_rate, phase == "background")',
    "normalized": "above_dark / max(above_dark)",
})
```

They take numbers, column names, `+ - * / **`, `abs sqrt exp log log10`, and
the per-run reductions `mean min max sum count first last`, each optionally
with a condition. A reduction is computed per run, so each run is subtracted
from its own background.

**Plots** are specs, the same ones the Data page will draw:

```python
df = load_plot({
    "runs": [41, 42, 43],
    "x": "bias_voltage",
    "y": ["count_rate"],
    "where": {"trigger_mV": {"per_run": "min"}},  # each run's own lowest level
    "series": "run",
    "label": "device",
})
```

`where` picks which points to draw; reductions in `x` and `y` still see the
whole run. `notebook_source(spec, db)` writes the Python that reproduces a
plot, which is what the Data page's "Open in notebook" will give you.

## Saving runs as files too

A **file saver** writes each run as a folder of CSV and YAML as well, for
people who work with files; see [Saving files](../wizard/data.md#saving-files). The folder
is a complete copy of the run's rows here, and `export_run(db, run_id, root)`
in `lab_wizard.lib.data.run_folder` writes the same folder for any recorded run.

## Why SQLite

For a dozen runs a day across a few cryostats, one file means trivial backups
(`cp data/lab.db backup.db`), no server to maintain, fine concurrent reads, and
durable writes during long measurements: each point and each step is its own
transaction, in WAL mode, so a crash loses at most the point being recorded.
Two runs on one machine can record at once.

## Schema changes

`meta.schema_version` is checked whenever the database is opened. A file from
another version, or one that is not a lab database at all, is refused with a
message naming both, and left untouched. There are no migrations yet; the first
change that needs one will add them.
