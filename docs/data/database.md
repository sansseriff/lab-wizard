---
icon: lucide/database
---

# The measurement database

!!! note "Two databases, for now"
    Every run started from a project is now recorded in the workspace's lab
    database, `data/lab.db` ([`lib/data/`](../../lab_wizard/lib/data/)), with
    one row per point, every step the run executed, and the filters the Data
    page will offer. See [Every run is recorded](../wizard/measurements.md#every-run-is-recorded).
    The `database_saver` below is the older, per-project database; it still
    works if a project selects it, and is being replaced
    (`plans/semantic_data_plan.md`).

## Reading runs back

Runs in the lab database are read with
[`lab_wizard.lib.data`](../../lab_wizard/lib/data/), which returns
[polars](https://pola.rs) frames. No SQL is involved:

```python
from lab_wizard.lib.data import find, facets, load_plot

runs = find(procedure="mcr_curve", device="A7")          # newest first
runs = find({"device.type": "SNSPD-A", "run.cryostat": "BlueFors1"})
runs = find({"param.readout.gate_time_s": {"range": [0.1, 1.0]}})

runs.table()     # one row per run: date, procedure, device, operator, points
runs.points()    # one row per point: run_id, seq, t, then every column
runs.steps()     # every step each run executed: the timeline
facets()         # every filter there is, with how many runs each leaves
```

`find` and `facets` use the workspace you are in (or `LAB_WIZARD_WORKSPACE`);
pass `db=` to name a `lab.db` directly. A filter is any facet: `procedure`,
`device`, `device.<property>`, `operator`, `date`, `run.<metadata key>`,
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

The `database_saver` persists measurement data to **SQLite** via SQLAlchemy. It
is the one fully-implemented saver. Schema:
[`savers/schema.py`](../../lab_wizard/lib/savers/schema.py); runtime:
[`savers/database_saver.py`](../../lab_wizard/lib/savers/database_saver.py).

## The core idea: flat data, decoupled from execution

Measurement *execution* is naturally hierarchical (sweep thermal powers, and at
each, sweep trigger levels). But the *data* is fundamentally flat: each
integration produces **one observation**, tagged with every condition under which
it was taken. Whether the inner loop was `thermal_power` or `trigger_level` is
invisible in the data — which is exactly what lets you slice the dataset
arbitrarily after the fact.

So: **one row per integration.** A "PCR curve" sweeping 20 trigger levels at 5
thermal powers is 100 rows. The curve doesn't exist as a stored object — it's a
query result.

## Schema

Six tables forming a hierarchy, with `cryostats` hanging off `runs`:

```mermaid
graph TD
    W[wafers] --> D[devices]
    D --> R[runs]
    C[cryostats] --> R
    R --> M[measurements]
    M --> MD[measurement_details]
```

| Table | One row = | Key columns |
|---|---|---|
| `wafers` | a fabricated wafer | `name`, `material` |
| `devices` | a device on a wafer | `wafer_id`, `name`, `pixel_geometry`, `width_nm` |
| `cryostats` | a cryostat | `name`, `location` |
| `runs` | one measurement program invocation | `cryostat_id`, `device_id`, `run_type`, `started_at`, `config` (JSON), `instruments` (JSON) |
| `measurements` | one row of a run: the readings taken at one set of parameter values | `run_id`, `timestamp`, `counts`, `int_time`, `temperature`, `data` (JSON), `metadata` (JSON) |
| `measurement_details` | sub-structure of one integration (histogram bins, time windows) | `measurement_id`, `detail_type`, `bin_index`, `value` |

Design principles baked into the schema:

- **Each fact lives in one place.** A run is one device, so `device_id` lives on
  `runs`, not on each measurement. "All measurements on device A7" is a join.
- **Real columns for what you query often** (`counts`, `temperature`,
  `int_time`); **JSON `metadata`** for the varying parameters that differ per
  run type (`bias_current`, `trigger_level`, `thermal_power`). A JSON field can be
  promoted to a real indexed column later if you query it constantly.
- **`config` is the measurement's own parameters** — the sweep, the gate time —
  and **`instruments` is what each instrument was configured with** when the run
  started, by name. The second exists because a project carries no copy of
  instrument settings: it names them and reads a config tree that is edited
  between runs, so without the snapshot nothing says which calibration a curve
  was taken at.
- **`run_type` is an Enum** (`pcr_curve`, `iv_curve`, `mcr_curve`,
  `extended_pcr`, `other`), which catches typos but also *loses information*:
  any composed procedure is stored as `other`. See the
  [Roadmap](../roadmap.md#savers-and-data).

Indexes exist on `runs(cryostat, started_at)`, `runs(run_type)`, and
`measurements(timestamp)`.

## What a procedure writes

**A procedure adds no columns.** The schema is fixed; a run's rows land in
`measurements.data` as JSON keys. A row is everything recorded while the same
parameter values were in force (see
[Procedures](../concepts/procedures.md#how-readings-become-rows)), so an
`mcr_curve` run over three attenuations writes four rows (abbreviated):

```json
{"phase": "background", "counts": 1, "int_time": 0.05, "count_rate": 20.0}
{"phase": "signal", "attenuation_db": 20.0, "counts": 387, "count_rate": 7740.0, "device_voltage": 0.0}
{"phase": "signal", "attenuation_db": 10.0, "counts": 4004, "count_rate": 80080.0, "device_voltage": 0.0}
{"phase": "signal", "attenuation_db": 0.0, "counts": 39165, "count_rate": 783300.0, "device_voltage": 0.0}
```

The swept value is on every row, so the loop nesting is invisible in the data,
which is the whole point. `measurements.metadata` holds the row's position in
the run (`seq`) and the steps that recorded into it (`steps`).

## Using it

Configure a `database_saver` instance on the [Data → Savers](../wizard/data.md)
page (`db_path`, `cryostat_name`), then select it when creating a measurement.
The runtime API:

```python
saver.start_run(run_type="pcr_curve", device="A7", cryostat="BlueFors-1",
                operator="me", config={"git_sha": "...", "bias": 12.5})
# ... per integration:
saver.write_measurement(
    counts=14823, int_time=1.0, delta_time=1.02, temperature=2.13,
    metadata={"thermal_power": 3.0, "trigger_level": 0.034, "bias_current": 12.5},
    details=[{"detail_type": "histogram_bin", "bin_index": i,
              "bin_value": centers[i], "value": hist[i]} for i in ...],
)
saver.end_run()
```

[`DatabaseSaver`](../../lab_wizard/lib/savers/database_saver.py) auto-creates the
cryostat and device by name on first reference, opens one `runs` row on
`start_run`, and commits **one `measurements` row per `write_measurement`** — so
each completed integration is durable on disk before the next starts (a real
liability mitigation against power blips and Ctrl-C). `end_run` stamps
`ended_at`.

## Why SQLite

For a dozen runs/day across a few cryostats: one file means trivial backups
(`cp measurements.db backup.db`), no server to maintain, fine concurrent reads,
and incremental durable writes during long measurements. SQLAlchemy abstracts the
backend, so moving to Postgres later (if many machines need concurrent writes)
is mostly a connection-string change.

## Schema changes

There is no migration framework. New **nullable** columns are added to an
existing database in place, by `add_missing_columns` when the saver opens it —
which is how a database written last month keeps working when a column like
`instruments` appears. Anything else (a type change, a non-null column) needs a
real migration and is deliberately not attempted.

!!! note "Reading the data"
    The old database has no read helpers; query it with `sqlite3` and
    `json_extract`. The lab database is read with `lab_wizard.lib.data`, above.
