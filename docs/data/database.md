---
icon: lucide/database
---

# The measurement database

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
| `measurements` | one observation | `run_id`, `timestamp`, `counts`, `int_time`, `temperature`, `data` (JSON), `metadata` (JSON) |
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

**A procedure adds no columns.** The schema is fixed; a run's observations land
in `measurements.data` as JSON keys. An `mcr_curve` run over three attenuations
writes seven rows that look like this (abbreviated):

```json
{"phase": "background", "counts": 1,     "int_time": 0.05, "count_rate": 20.0}
{"phase": "signal", "attenuation_db": 20.0, "counts": 387, "count_rate": 7740.0}
{"phase": "signal", "attenuation_db": 20.0, "device_voltage": 0.0}
{"phase": "signal", "attenuation_db": 10.0, "counts": 4004, "count_rate": 80080.0}
...
```

Two things to notice. The swept value is copied onto every row, so the loop
nesting is invisible in the data — which is the whole point. But a `count` and a
`read_voltage` at the same attenuation are **two rows**, because each
`observe()` writes one; reassembling a point means matching on `attenuation_db`.
That is a known divergence from *one row per integration*, designed in
`plans/semantic_data_plan.md`.

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
    [`query.py`](../../lab_wizard/lib/savers/query.py) has pandas helpers
    (`get_measurements`, `get_runs`), but pandas is not a dependency of this
    package, and the `measurements_full` view described in
    `plans/database_plan.md` does not exist. Querying with `sqlite3` and
    `json_extract` works today — see the [Roadmap](../roadmap.md).
