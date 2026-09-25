# The lab data system: recording runs, saving files, and the viewer

> **Status: Phases 1–4 built (2026-09-24); Phases 5–8 not started.** Decisions
> still marked **(open)** in §14 need an answer; everything else was settled
> in review.
>
> **This is a startup document.** It is written for an engineer or agent picking
> this up cold. Read it end to end before changing anything.
>
> It supersedes the schema in [`database_plan.md`](database_plan.md). Running a
> project from the GUI and live plotting are planned in
> [`runner_plan.md`](runner_plan.md); both consume the stream described in §4.
> No backwards compatibility with databases written before this work is
> required: existing `measurements.db` files are abandoned, not migrated.

---

## 1. What this is for

A **Data** page in the lab_wizard GUI that is better than browsing a tree of CSV
files. A lab member must be able to:

1. **Filter all the workspace's runs** from a sidebar, in many ways at once: all
   runs on one device type, all runs that used a particular laser, all MCR
   curves, everything from last Tuesday.
2. **See what a run produced**: a plot chosen by the procedure's author, shown
   by default when a run is selected.
3. **Overlay runs**: plot the same quantity from several runs, including a
   subset of each ("the lowest trigger level of each of these five runs").
4. **Put any column on any axis.** A measured voltage against another measured
   voltage is a normal plot.
5. **Plot simple derived quantities** (background-subtracted rate, normalized
   efficiency) without leaving the viewer.
6. **Reconstruct what happened**: the procedure's structure, the order and
   timing of every step, which step produced which data.
7. **Hand off to a notebook** with one click, landing where the viewer stopped.

And, because many lab members are used to files:

8. **Optionally save every run as a folder of plain files** (CSV and YAML) that
   opens without lab_wizard.

Nobody should ever have to write SQL.

---

## 2. The decisions

| # | Decision | Why |
|---|---|---|
| D1 | **One database per workspace**, at `<workspace>/<data_dir>/lab.db`. Synchronization across computers is future work. | "All the lab's data" cannot be filtered across per-project files. |
| D2 | **Runs are what you filter; points are what you plot.** | Every filter anyone asked for is a fact about a run. |
| D3 | **Only structural columns are typed.** Every measured or swept value lives in one JSON `values` object per point. | One shape; no procedure ever changes the schema; no orphaned columns. |
| D4 | **A point row is everything recorded while the same parameter values were in force, until a field would be recorded twice.** (§5) | Loop order does not change the data; readings taken together share a row. |
| D5 | **The execution is recorded**: a `steps` row per step execution, and every point names its steps. (§6) | The run replays as a timeline; loop order stays recoverable. |
| D6 | **The sidebar is faceted search** over a derived `run_facets` table. (§8) | New procedures and instruments become filters with no code change. |
| D7 | **No axis roles.** The procedure author declares default `plots:`; any column may go on any axis. (§9) | Inference cannot know an IV curve's x is sometimes a read voltage. |
| D8 | **Derived quantities are computed when read, never stored**, from a small restricted expression language. (§10) | Fixing a formula fixes every past run. |
| D9 | **The viewer supports nothing it cannot export as code.** (§11) | Sets the viewer's scope; the notebook handoff is lossless. |
| D10 | **Arrays are stored as arrays** inside `values`. `measurement_details` is deleted. | Simple; a 4096-bin histogram is ~20 KB. |
| D11 | **Setter steps may record the value they reached** (`record:`). | "The attenuation actually reached" is a measurement. |
| D12 | **`Repeat` binds a named parameter**, like `Sweep`. | Otherwise repeated rows are identical in `values`. |
| D13 | **One stream, many sinks.** A run emits one stream of messages; the database recorder, file saver, plotters and the GUI's live view all subscribe to it. (§4) | Every consumer sees the same rows, built once. |
| D14 | **The database is always written; it is not a "saver" option.** Savers are the *optional* extra outputs, starting with the file saver. (§7) | A run missing from the database is missing from the viewer. |
| D15 | **A run folder is a lossless copy of a run's database rows.** The file saver, the viewer's "Export run" and a future "Import run" share one format. (§7) | One format, three uses; files are never a second-class record. |
| D16 | **All plotting uses one plot evaluator**, from `lab_wizard.data`: the viewer, the matplotlib window and the web plot all turn a plot spec plus points into series the same way. | A plot looks the same live and afterwards. |

---

## 3. The schema

Five tables in one SQLite file. JSON columns hold dicts; every plain column is
something **every** run or point has.

```
meta
  key TEXT PRIMARY KEY, value TEXT          -- schema_version, created_at

devices
  id          INTEGER PRIMARY KEY
  name        TEXT UNIQUE NOT NULL          -- "A7"
  properties  JSON                          -- {"type":"SNSPD-A","wafer":"W12","width_nm":80}
  notes       TEXT

runs
  id            INTEGER PRIMARY KEY
  procedure     TEXT NOT NULL               -- "mcr_curve"; any string
  status        TEXT NOT NULL               -- running | success | failed | aborted
  started_at    TEXT NOT NULL               -- UTC ISO-8601
  ended_at      TEXT
  device_id     INTEGER REFERENCES devices
  operator      TEXT
  notes         TEXT
  project       TEXT                        -- project directory name
  metadata      JSON                        -- the project's run.metadata: {"cryostat":"BlueFors1"}
  definition    JSON                        -- the procedure definition, snapshotted
  params        JSON                        -- the params it ran with
  instruments   JSON                        -- {role: {type, key, attribute_name, params}}
  columns       JSON                        -- {name: {unit, steps:[...], bins?}}

steps
  id          INTEGER PRIMARY KEY           -- insertion order = start order
  run_id      INTEGER NOT NULL REFERENCES runs
  path        TEXT NOT NULL                 -- see §6
  kind        TEXT NOT NULL                 -- the definition's step type
  started_at  TEXT NOT NULL
  ended_at    TEXT
  status      TEXT                          -- success | failed | aborted
  error       TEXT

points
  run_id  INTEGER NOT NULL REFERENCES runs
  seq     INTEGER NOT NULL                  -- 0, 1, 2 ... in recording order
  t       TEXT NOT NULL                     -- when its last reading was taken, UTC
  steps   JSON NOT NULL                     -- paths of the steps that contributed
  values  JSON NOT NULL                     -- {"phase":"signal","attenuation_db":20.0,"counts":387}
  PRIMARY KEY (run_id, seq)

run_facets                                  -- derived; rebuildable at any time
  run_id  INTEGER NOT NULL REFERENCES runs
  key     TEXT NOT NULL                     -- "device.type"
  value   TEXT NOT NULL                     -- "SNSPD-A"
  num     REAL                              -- value as a number, when it is one
  PRIMARY KEY (run_id, key, value)
  INDEX (key, value), INDEX (key, num)
```

Deleted outright: `measurements`, `measurement_details`, `wafers`,
`cryostats`, the `RunType` enum, `add_missing_columns`. A wafer is a device
property; a cryostat is run metadata.

**Versioning.** `meta.schema_version` is checked on open; a mismatch refuses to
open, naming both versions. Migrations are deferred until a change needs one
(§14, Q2).

**SQLite settings.** WAL mode, `foreign_keys=ON`, one connection per run
process, one short transaction per point.

### Where the database lives

The workspace manifest `lab-wizard.toml` gains a data directory, beside the
paths it already has:

```toml
[workspace]
config_dir = "config"
projects_dir = "projects"
logs_dir = "logs"
data_dir = "data"          # new: lab.db, and the file saver's default root
```

A run finds its workspace from the project directory with
`workspace.find_workspace` (which reads the manifest and honours
`LAB_WIZARD_WORKSPACE`) and records to `<data_dir>/lab.db`.

A project **outside any workspace** (the `pedagogical_embedded` generation
style, whose point is that the folder is all it needs) records to
`<project>/data/lab.db`: the project folder is treated as its own small
workspace. No configuration is involved in either case.

This replaces today's arrangement, where `db_path: measurements.db` is copied
from `config/savers/` into every project. Its description says "relative to the
project", but `DatabaseSaver` passes it straight to SQLite, so it is relative to
**whatever directory the process was started in**. That is why an empty
`measurements.db` sits at the repository root.

**Two workspace-discovery functions exist and disagree.**
`lab_wizard/wizard/workspace.py::find_workspace` reads the manifest.
`lab_wizard/lib/client/server_discovery.py::find_workspace_config_dir` instead
walks upward for a directory literally named `config` containing `server/` or
`instruments/`, so it ignores the manifest's `config_dir` setting and the
environment variable. The recorder needs the manifest (for `data_dir`), so
`workspace.py` moved into `lab_wizard/lib/` (the library must not import the
GUI package). **Replacing `find_workspace_config_dir` is deferred:** instrument
resolution depends on it, and many test fixtures (and any workspace made before
the manifest existed) have a `config/` directory and no `lab-wizard.toml`, so
switching it is its own change with its own tests.

(`config/server/` is unrelated to data. It holds the *instrument* server's
`server.yaml`, with its bind address and permissions, plus its pid file, log and
event audit log. `wizard init` creates it empty; the wizard writes `server.yaml`
when the user enables the server, see `wizard/backend/server_control.py`. Its
only overlap with this work is that `find_workspace_config_dir` uses its
existence as a sign that it has found a workspace.)

---

## 4. One stream, many sinks

A run already emits messages on two buses (`RunContext.data_bus`,
`status_bus`). After this work the messages are:

| Message | Carries |
|---|---|
| `RunStarted` | procedure, device, operator, notes, metadata, definition, params, instruments, columns |
| `Point` | seq, t, steps, values (one closed row, §5) |
| `StepBegan` / `StepEnded` | path, kind, time, status, error |
| `StepProgress` | path, fraction, detail (live only; not recorded) |
| `RunEnded` | status, time |

Every output is a sink subscribed to that stream:

| Sink | When | Where |
|---|---|---|
| `DatabaseRecorder` | always | §3 |
| `FileSaver` | when the project configures one | §7 |
| `MplPlotter`, `WebPlotter` | when the project configures one | `runner_plan.md` |
| `EventPublisher` (websocket) | when the wizard launched the run, or a web plotter needs it | `runner_plan.md` |

Generated projects stop wiring sinks by hand. `run_measurement` calls one
library function, `run_procedure(tree, resources, procedure=, definition=,
project_dir=)` in `lab_wizard/lib/task_adapters/run.py`, which builds
`RunStarted` from the project, calls `attach_sinks` (the recorder, then the
configured savers and plotters) and runs. Changing what a sink does never means
regenerating projects.

**A run started from a project is always recorded; a bare step tree is not.**
`run_procedure` records when it is given `project_dir`, which a generated
module passes as its own folder. Tests and notebooks that run a tree directly
record nothing unless they attach a `DatabaseRecorder` themselves.

The recorder is subscribed first, so its `run_id` exists before any other sink
sees the run start. It is exposed as `DatabaseRecorder.run_id`; putting it on
the messages themselves waits for the first sink that needs it (the event
stream in `runner_plan.md`).

---

## 5. The point rule

> A row is everything recorded while the same parameter values were in force,
> until a field would be recorded twice.

"Parameter values in force" is `RunContext.snapshot_parameters()`: whatever
`Sweep`, `WithParameter` and (after D12) `Repeat` have bound at the moment of
the `observe()` call.

On each `observe(fields)`:

- if a row is open, the parameters equal the open row's, **and** none of
  `fields` is already on the row → merge into the open row;
- otherwise close the open row (emit a `Point`) and open a new one with the
  current parameters plus `fields`.

A row also closes as soon as the parameters it was recorded under go out of
force, which is the end of its loop iteration (`RunContext.bound_parameter`).
The rows are the same either way; this only makes each one appear when it is
complete instead of when the next reading arrives, which a live plot needs.

When the run ends (success, failure, abort or exception) the open row is
closed **before** `RunEnded`.

A step may not record a field named like a parameter in force: the row already
carries the parameter, and a reading under the same name would silently replace
it. `observe()` raises `ValueError`.

### Worked example: loop order does not change the rows

PCR, bias ∈ {0.02, 0.03} V, trigger ∈ {−50, −40, −30} mV, with `count` and
`read_voltage` in the innermost loop. With trigger as the inner loop, twelve
observations merge into six rows:

| seq | bias_voltage | trigger_mV | count_rate | device_voltage |
|---|---|---|---|---|
| 0 | 0.02 | −50 | 33333.3 | 0.0 |
| 1 | 0.02 | −40 | 40000.0 | 0.0 |
| 2 | 0.02 | −30 | 46666.7 | 0.0 |
| 3 | 0.03 | −50 | 50000.0 | 0.0 |
| 4 | 0.03 | −40 | 60000.0 | 0.0 |
| 5 | 0.03 | −30 | 70000.0 | 0.0 |

With bias as the inner loop, the same six rows arrive with different `seq`. The
guarantee: **two procedures that take the same readings at the same parameter
values produce the same set of rows, whatever the loop order.** Order lives in
`seq`, `t` and `steps`, never in the shape of `values`.

A reading placed at an outer loop level genuinely happens fewer times, so it
lands on its own row with fewer parameters (`{bias_voltage, device_voltage}`).
That is correct: the procedure measured something different.

### Edge cases

| Situation | Outcome |
|---|---|
| mcr background count (in no loop) | `{phase: background}` is its own key → one row |
| `repeat: 10` | binds `repeat` = 0…9 (D12) → ten distinct rows |
| sweep up then down (hysteresis) | the second visit to a value is a new row, told apart by `seq` |
| `retry` around `count` | a failed attempt records nothing → one row |
| two steps recording the same field under one set of parameters | two rows; `definition.check()` warns (§13 Phase 4) |
| a setter with `record:` | its read-back opens the row; the count and voltage join it |
| a sweep that lists the same value twice | two rows, since the first closes when its iteration ends |

### Where it lives

In `RunContext`, before the data bus, so every sink sees whole rows.
`RunContext.latest` keeps updating on **every** `observe()`, not at close, so
condition steps (`value_above`) read what was just measured.

---

## 6. Recording the execution

`Step.execute` emits `StepBegan`/`StepEnded`, timestamped, with a node path
([core.py](../procedure_framework/lab_procedure/core.py)) on the status bus.
Nothing outside the tests consumes them yet; the wizard has no live progress
view (that is `runner_plan.md`). The recorder will subscribe to them and write
one `steps` row per execution.

**Paths.** The runtime tree mirrors the definition tree one to one; the only
extra level is a repeated child. `[n]` is a child's position among its
parent's children; `#n` marks the nth run of a child that a sweep, repeat or
retry runs again. The real mcr count at the third attenuation is:

```
sequence/source_guard[1]/safe_guard[0]/sequence[0]/with_parameter[4]/sweep[0]/sequence#2/count[2]
```

A segment is the step's `name:` when its author set one, otherwise its kind
(`SourceGuard` → `source_guard`, the definition's step type), so a path can be
walked against `runs.definition`.

**Points name their steps.** `RunContext` keeps the executing step's path,
pushed and popped by `Step.execute`; each `observe()` appends it to the open
row's `steps`.

---

## 7. File saving

For lab members who want files. The file saver writes a **run folder** while
the run happens. It is the same data as the database rows, laid out for a file
browser and a spreadsheet.

### What a run folder contains

```
2026-09-22/mcr_curve_A7_143012/
  run.yaml          # everything on the runs row: procedure, device, operator,
                    #   times, status, metadata, params, instruments, columns (with units)
  points.csv        # one line per point: seq, t, then one column per recorded name
  steps.csv         # the timeline: path, kind, started_at, ended_at, status, error
  procedure.yaml    # the definition snapshot
  arrival_time.csv  # one file per array column: seq, then one column per bin
  plot.png          # optional: the default plot, drawn at the end
```

`points.csv` rows are the §5 rows exactly, so a file user gets one line per
point with every reading at that point on it. Missing values are empty cells.
The column order is: `seq`, `t`, swept parameters in tree order, then recorded
fields in tree order, all taken from `columns`.

### The folder hierarchy is a choice of facets

A folder tree can only be ordered one way. The saver builds each run's path
from a template whose fields are **facet keys** (§8):

```yaml
savers:
  files:
    type: file_saver
    root: ""                                   # empty = <workspace>/<data_dir>/files
    path: "{date}/{procedure}_{device}_{time}"  # any facet keys
    plot_png: true
```

`{date}/{procedure}_{device}_{time}` is the default. A lab that thinks by device
writes `{device.wafer}/{device}/{date}_{procedure}`. This is the whole trade in
one line: files commit to one ordering of the facets, while the viewer can
filter by any of them in any order.

### Writing while the run happens

- `run.yaml` and `procedure.yaml` are written at `RunStarted`; `run.yaml` is
  rewritten at `RunEnded` with the status and end time.
- `points.csv` and `steps.csv` are appended and flushed per row, so a crash
  loses at most the open point.
- The header comes from `columns`, which is known at `RunStarted` from the
  definition. If a field not in `columns` turns up anyway, the file is rewritten
  once at `RunEnded` with the complete header.

### One format, three uses (D15)

The same writer backs the file saver, the viewer's **Export run** (for someone
who wants files from a run that was not saved as files), and later **Import
run** (bringing a folder from another computer or an embedded project into a
workspace database). Import is out of scope now, but the format must stay
lossless so it remains possible: everything on the `runs`, `steps` and `points`
rows must be in the folder.

---

## 8. Facets: how the sidebar works

When a run ends, the recorder flattens its facts into `run_facets`:

| Facet key | From |
|---|---|
| `procedure`, `status`, `operator`, `project` | `runs` columns |
| `date` | `started_at`, as the lab's **local** date (a folder template's `{time}` is computed by the file saver, not a facet: one value per run filters nothing) |
| `device` | `devices.name` |
| `device.<prop>` | each scalar in `devices.properties` |
| `run.<key>` | each scalar in `runs.metadata` (`run.cryostat`) |
| `instrument.<role>.type`, `instrument.<role>.class` | `runs.instruments`: the config type when the params say it, and the Python class always |
| `instrument.<role>.<param path>` | each scalar leaf of that instrument's params |
| `param.<path>` | each scalar leaf of `runs.params` |
| `column` | one row per recorded column ("runs that recorded `device_voltage`") |

Only scalar leaves become facets. `num` is filled when the value parses as a
number, so a numeric facet with many values can be a range slider.

The sidebar is one query shape (facet values with counts under the current
filters), so it **builds itself**: the first run on a new instrument type adds
that filter.

`run_facets` is a cache, rebuilt for a run when it ends, for a device's runs
when that device is edited, and for everything by a `rebuild` command.

**The honest limit.** A filter finds only what was recorded. "Every run with the
4.5 µm QCL" needs the wavelength to be a param of that instrument's config;
"every run on SNSPD-A devices" needs the device named at run start (§12).

---

## 9. Default plots

A procedure definition gains an optional `plots:` list. The first is what the
viewer shows for a run, and what a live plotter draws; the others are tabs.

```yaml
plots:
  - name: MCR
    x: attenuation_db
    y: [count_rate]
    where: {phase: signal}
    log_y: true
  - name: Attenuator linearity
    x: attenuation_db
    y: [attenuation_db_reached]
  - name: Stayed superconducting
    x: attenuation_db
    y: [device_voltage]
```

**Fallback** with no `plots:`: x = the innermost swept parameter, y = the first
recorded field.

The viewer uses the **current** definition's `plots:` and `derived:`, so a plot
added later applies to past runs; if the procedure was deleted, the snapshot in
`runs.definition`. The composer gets a Plots panel, and a plot customized in the
viewer can be saved back into the procedure.

---

## 10. Derived quantities

A procedure gains an optional `derived:` map; the viewer also accepts ad-hoc
expressions.

```yaml
derived:
  rate_minus_dark: count_rate - mean(count_rate, phase == "background")
  normalized: rate_minus_dark / max(rate_minus_dark)
```

**The language, completely:**

- numbers, strings, column names (recorded or derived);
- `+ - * / **`, unary `-`, parentheses;
- comparisons `== != < <= > >=` and `and or not`, only inside a reduction's
  condition;
- row functions `abs sqrt exp log log10`;
- per-run reductions returning one number: `mean min max sum count first last`,
  each `f(expr)` or `f(expr, condition)`.

Parsed with Python's `ast` against a whitelist of node types (never `eval`) and
compiled to a **polars expression**, evaluated on a DataFrame holding the
points of one or more runs. Per-run reductions map directly onto polars window
expressions:

| Expression | Polars |
|---|---|
| `count_rate / int_time` | `pl.col("count_rate") / pl.col("int_time")` |
| `max(x)` | `pl.col("x").max().over("run_id")` |
| `mean(count_rate, phase == "background")` | `pl.col("count_rate").filter(pl.col("phase") == "background").mean().over("run_id")` |

So a spec over five runs is one lazy query, not a Python loop over runs.
Derived values are never stored.

Anything else belongs in a notebook: fits (including jitter FWHM), arithmetic
between runs, joins against calibration data, anything iterative.

---

## 11. Plot specs, overlay and the evaluator

A **plot spec** says what to draw:

```yaml
runs: [41, 42, 43, 44, 45]
x: bias_voltage
y: [count_rate]
y2: []                          # secondary axis
where:
  phase: signal                 # equals
  trigger_mV: {per_run: min}    # that run's own lowest trigger level
series: run                     # or any column
label: device                   # legend text from a facet key
connect: seq                    # seq (default) | x | none
kind: line                      # line | scatter | histogram | waterfall
log_x: false
log_y: false
```

`where` accepts per column: a value, `{in: [...]}`, `{range: [lo, hi]}`,
`{per_run: min | max | first | last}`. `x`, `y` and `where` columns may be
derived expressions.

**`where` picks the points to draw; it does not change what a reduction sees.**
Every expression is evaluated over whole runs and the rows are filtered after,
so `y: count_rate - mean(count_rate, phase == "background")` with
`where: {phase: signal}` draws the signal subtracted by its own background.
(Filtering first would remove the background and give nothing.)

- One run selected → the spec starts as that run's first plot.
- More runs selected → they join `runs:` and `series` becomes `run`.
- Overlay matches columns by name. Differently named columns across procedures
  are a notebook job.
- `connect: seq` joins points in recording order within a series, which is what
  shows a hysteresis loop; sorting by x would hide it.
- Array columns use `kind: histogram` (one point) or `waterfall` (points ×
  bins); a step recording an array declares its bins in `columns`
  (`"bins": {"start": 0, "step": 4, "unit": "ps"}`).

**The evaluator** (D16) is one function in `lab_wizard.lib.data` (`evaluate_plot`): plot spec + a
polars DataFrame of points (with a `run_id` column) → series ready to draw. The viewer calls it on
database rows. A live plotter calls it on the rows so far, recomputing the whole
thing (throttled) as each point arrives, which keeps per-run reductions such as
`max(...)` correct while the run grows.

**Open in notebook** writes:

```python
from lab_wizard.lib.data import load_plot
spec = {...}                     # the spec, verbatim
df = load_plot(spec)             # polars DataFrame: one row per plotted point, with run_id and series

import matplotlib.pyplot as plt  # matplotlib is already a dependency
for (series,), part in df.group_by("series", maintain_order=True):
    plt.plot(part["x"], part["y"], label=series)
plt.legend()
```

(Not `df.plot`: polars' plot namespace needs `altair`, which lab_wizard does not
depend on.)

---

## 12. Run start: what gets captured

A run records only what it is told; today every run's `device_id` is NULL.

- **Project YAML `run:` block**:

  ```yaml
  run:
    device: A7                # name in the devices table; required
    operator: andrew
    notes: ""
    metadata: {cryostat: BlueFors1}
  ```

  `Device` in `model_tree.py` shrinks to a name. Properties live in the
  `devices` table, editable in the viewer, and apply to every past run.
- **Generated projects** read the `run:` block at each run start and pass it to
  `RunStarted`. Codegen passes none of it today
  ([codegen.py](../lab_wizard/lib/procedures/codegen.py), `run_measurement`).
- **The wizard** asks for the device when generating a project and shows it on
  the project page, since people swap devices between runs of one project.
- **The instrument snapshot** (`provenance.baseline_snapshot`) is keyed by
  **role** and records each instrument's `class`, config `type`,
  `attribute_name` and params. An instrument whose params cannot be read (some
  modules and channels today) is still listed by class. The config key (the
  hash) is not available from an instrument, so it is not recorded.
- **Units.** `emits` changes from a tuple of names to `{name: unit}`;
  `ReadVoltage` takes a unit with its field. Swept columns take the unit of the
  sweep param (`ParamDecl.unit`). Together they fill `columns`.

---

## 13. The work, in order

Each phase leaves the repo working and tested. Running from the GUI and the
plotters follow in `runner_plan.md`, after Phase 3.

### Phase 1: the framework (`procedure_framework/lab_procedure`) — built

- `RunContext`: the point rule (§5); the step path stack; closing the open row.
  Delete `sweep_index` and `set_parameter`.
- `Point` replaces `Observation`: `{seq, t, steps, values}`.
- `ProcedureRunner.run`: close the open row before `RunEnded`, on every path.
  A Ctrl-C (`KeyboardInterrupt`) must end the run as `aborted`; today it
  passes the `except Exception` and `RunEnded` reports `failed`.
- `Step.execute`: push/pop the path; timestamps on step messages; `#n`
  iteration labels (§6).
- `Repeat` binds a parameter (default `repeat`).
- Tests: the two loop orders give the same set of rows; the outer-level read
  gets its own row; mcr gives 5 rows; repeat, retry, background, abort
  mid-point.

As built: `tests/test_point_rows.py` covers the rule. `iv_curve` and
`pcr_curve` emitted `Observation`s directly and now call `observe()`, so they
go through the rule until they are ported (Phase 4). `SaverSink` and
`PlotterSink` were adapted just enough to read `Point`; `DatabaseSaver` writes
`values` as `data` and `{seq, steps}` as `metadata`. The `repeat` step schema
gained `parameter`, so `emitted_fields()` lists it. Generated projects under
`projects/` that predate this import `Observation` and must be regenerated.

### Phase 2: recording (`lab_wizard/lib/data/`, new) — built

- Move `workspace.py` into `lab_wizard/lib/`; replace
  `find_workspace_config_dir`; add `data_dir` to the manifest.
- `schema.py` (§3) with the version check.
- `recorder.py`: the `DatabaseRecorder` sink; facets at run end.
- `sinks.py`: `attach_sinks`.
- Run start capture (§12).
- Tests: a generated mcr project run as a script, rows read back; facets for
  device, instrument, params; an embedded project records into its own folder.

As built:

- `lab_wizard/lib/data/`: `schema.py` (plain `sqlite3`, WAL, version check that
  refuses rather than alters), `recorder.py`, `facets.py`, `encoding.py`
  (NaN → null, numpy → lists and numbers). `run_procedure` and `attach_sinks`
  live in `lab_wizard/lib/task_adapters/run.py` beside the other run plumbing.
- `workspace.py` moved to `lab_wizard/lib/workspace.py` with `data_dir`;
  `clean_workspace` and `wizard clean` never touch it.
  `find_workspace_config_dir` stays for now (§3).
- `RunStarted` is `{procedure, device, operator, notes, project, metadata,
  definition, params, instruments, columns, t}`. `StepBegan` carries `kind`;
  `StepEnded` carries `error`.
- `RunConfig` is `{device, operator, notes, metadata}`; the generators write
  them empty. **Not built: the GUI asking for the device.** Until it is, the
  `run:` block is edited in the YAML (documented in `docs/wizard/measurements.md`).
- Generated modules carry `DEFINITION` in a `wizard:definition` block,
  regenerated with the step tree by `refresh_procedure_source`, and
  `run_measurement` is one `run_procedure` call. `iv_curve`/`pcr_curve` take
  `project_dir` from their setup script (their module is copied into the
  project, so `__file__` cannot be trusted) and record `definition: null`.
- `runs.columns` is the declared columns plus any recorded column nobody
  declared, so a hand-written measurement's columns are still listed.
- `ProcedureDefinition.columns()` gives units for swept columns (from their
  sweep param); recorded fields get units in Phase 4.
- The old `DatabaseSaver` still runs if a project selects it (it writes its own
  `measurements.db`) until Phase 5 replaces it.
- Tests: `tests/test_data_recording.py`; the own-server end-to-end test now
  reads the workspace database (device, operator, cryostat, role-keyed
  provenance through the server, steps, facets); the mcr script test checks a
  project outside a workspace records into its own `data/lab.db`.

### Phase 3: reading (`lab_wizard/lib/data/`)

- `find(**filters)`, `facets(filters)`, `Runs.points()` → a polars DataFrame,
  one row per point, `run_id` and `seq` first, then one column per name in
  `values`.
- The expression evaluator (§10), with tests that it rejects everything outside
  the whitelist.
- The plot evaluator and `load_plot(spec)` (§11), and notebook text for a spec.
- Replaces `lib/savers/query.py`, which imports pandas; pandas is not a
  dependency and is not installed, so `query.py` cannot be imported today.
- Add `polars` to `lab_wizard/pyproject.toml`. pandas is not used anywhere in
  the new code.

**Building the DataFrame from `values`.** Points are ragged (a field can be
missing from a row) and may hold arrays. Build the frame from the rows with the
schema taken from `runs.columns` rather than inferred, so a column absent from
the first rows is still typed correctly and missing values are nulls. Array
columns become polars `List(Float64)`, which the waterfall plot uses directly.

As built — Phase 3:

- `lab_wizard/lib/data/`: `read.py` (`Lab`, `Runs`, `find`, `facets`,
  `lab_database`), `expressions.py` (`compile_expression`, `derive`), `plot.py`
  (`PlotSpec`, `evaluate_plot`, `load_plot`, `to_series`, `notebook_source`).
  The import path is `lab_wizard.lib.data`, not `lab_wizard.data`.
- `columns` records only units, so types are inferred over **all** rows
  (`infer_schema_length=None`) rather than taken from it. That still types a
  column absent from the first rows; a column mixing text and numbers becomes
  text.
- Filters are facet keys: a value, a list (any of), or `{"range": [lo, hi]}`
  on `num`; keys combine with AND. `facets(filters)` counts each key's values
  ignoring that key's own filter, so a chosen procedure still shows the others.
- `Lab` opens a connection per query, so a `Runs` kept in a notebook holds
  nothing open and sees runs recorded since.
- `evaluate_plot` returns one row per point (`run_id, seq, series, axis,
  y_name, x, y, z`); `to_series` groups it into JSON-ready series for a browser
  or a live plotter. Histogram and waterfall kinds spread an array column over
  its bins; nothing records an array yet.
- The notebook export draws with matplotlib (`y2` on a twin axis).
- Tests: `tests/test_data_reading.py`, plus the mcr script test reading its own
  recorded run back as a background-subtracted plot.

### Phase 4: procedure definitions and ports — built

- `plots:`, `derived:`; `check()` verifies their columns exist.
- `emits` with units; `record:` on `set_attenuation`, `set_voltage`,
  `set_threshold`, `set_laser_power`.
- `check()` errors on nested sweeps binding the same name and warns when two
  steps record the same field in one loop body.
- Composer: Plots and Derived panels. `mcr_curve.yml` gets its plots.
- **Port `iv_curve` and `pcr_curve` to procedure definitions**, as `mcr_curve`
  was, and delete `lib/measurements/iv_curve` and `pcr_curve`. Their end-to-end
  tests move to the procedure form.

As built:

- `ProcedureDefinition.plots` is a list of `PlotSpec` (from `lib/data/plot.py`)
  with no `runs`; `derived` is `{name: expression}`. Both round-trip through the
  generated module's `DEFINITION` block, so every run records them.
- **Units sit beside `emits`**, as a `units` mapping on each step schema, rather
  than turning `emits` into a mapping: `emits` is read as a list of names by the
  frontend catalog and by `main.py`. `emitted_units()` gives
  `{field: unit}`; `read_voltage` records volts.
- **`record:` is on `set_attenuation`, `set_threshold` and `set_laser_power`
  only.** `VSource` has no getter, so `set_voltage` cannot report what it holds.
- **`param("path")`** joined the expression language: a number from each run's
  own params, per run. `iv_curve`'s `current` needs it (the bias resistance).
  `Runs.params()` supplies them; `Runs.derived()` merges the recorded
  definitions' `derived:`, and `load_plot` applies both. The viewer reading the
  *current* definition's plots (§9) is the backend's job in Phase 6; the
  library uses the definition each run recorded.
- `check()` also errors on a step recording a field named like a parameter an
  enclosing step binds (what `observe()` would raise at run time), and on plots
  or derived columns naming unknown columns, undeclared params, non-number
  params, cycles, or a plot with `runs`. **Warnings** (`definition.warnings()`,
  and a `warnings` list from `POST /api/procedures/check` and the procedure
  list) cover two steps recording one field in a loop body; `if` and
  `selector` branches count as alternatives.
- Composer: a **Plots** tab (`PlotsEditor.svelte`) edits plots and derived
  columns; `where` offers equals and per-run min/max/first/last (ranges and
  lists through the YAML tab). Warnings are listed under the problems.
  Type-checked and unit-tested; **not yet clicked through in a browser**, and
  prettier could not check `.svelte` files here (it crashes under bun, and
  there is no node on this machine).
- `iv_curve` and `pcr_curve` are in `lib/procedures/library/` with the same
  params as before (PCR's unused `photon_rate_hz` dropped), plus plots;
  `mcr_curve` gained plots, `rate_above_dark` and `attenuation_db_reached`.
- **Not done: removing the hand-written measurement kind.** With nothing left
  under `lib/measurements/` it offers nothing, but `get_measurements.py`,
  `generate_measurement_project`, `kind="measurement"` and their endpoints
  remain. Removing them means editing `main.py`, which was being changed in
  parallel; it is a self-contained cleanup for later.
- Tests moved to the procedure form: `test_iv_curve_end_to_end.py` now runs the
  generated project, records, and checks the *derived* current against the
  detector model; `test_lab_wizard_measurements.py` builds each generated module
  in memory and runs it on stand-ins; generation tests call
  `generate_procedure_project`. New: `tests/test_procedure_data_model.py`.

### Phase 5: the file saver (`lab_wizard/lib/savers/file_saver.py`)

- `FileSaverParams` (§7) as the saver resource type; `DatabaseSaver` and its
  params are deleted.
- The run-folder writer, shared with Export run.
- Tests: a run folder round-trips everything on its database rows; the path
  template; crash mid-run leaves a readable folder.

### Phase 6: backend API (`lab_wizard/wizard/backend/data_api.py`, new)

| Endpoint | Returns |
|---|---|
| `GET /api/data/facets?<filters>` | facet keys, values and counts under the filters |
| `GET /api/data/runs?<filters>&page=` | run summaries |
| `GET /api/data/runs/{id}` | params, instruments, columns, plots, derived |
| `GET /api/data/runs/{id}/steps` | the timeline |
| `POST /api/data/plot` | series for a spec |
| `POST /api/data/plot/notebook` | Python text for a spec |
| `POST /api/data/runs/{id}/export` | a run folder, zipped |
| `GET/PUT /api/data/devices` | the device registry |
| `POST /api/procedures/{name}/plots` | save a spec into a procedure |

### Phase 7: the viewer (`routes/data/database`, replacing the stub)

- **Left:** facet sidebar, grouped (Procedure, Device, Device properties, Run,
  Instruments, Params, Operator, Date, Status), with a search over facet keys.
- **Middle:** run list (date, procedure, device, operator, status, points),
  multi-select.
- **Right:** plot panel: the run's first plot, tabs for the others, a spec
  editor, "Open in notebook", "Export run". Timeline and Details tabs.
- A running run refreshes on a poll.
- Clicking a point shows its steps and highlights them in the timeline.
- The plot itself is the shared BokehJS component from `runner_plan.md`.

### Phase 8: cleanup

- `lib/savers/schema.py`, `database_saver.py`, `query.py`; `SaverSink`,
  `PlotterSink` (replaced by sinks in `attach_sinks`).
- `docs/data/database.md` rewritten; the Database page's "one row per
  integration" copy removed.
- Tests rewritten: `test_lab_wizard_task_adapters.py`,
  `test_lab_wizard_measurements.py`, `test_own_server_projects.py`,
  `test_provenance.py`.

---

## 14. Decisions

Settled in review:

- **One database per workspace**; synchronization across computers later.
- **Savers stay** as a configurable resource, meaning optional extra outputs;
  the database is always written.
- **Plotters stay**: a native matplotlib window and a web plotter
  (`runner_plan.md`).
- **`iv_curve` and `pcr_curve` are ported** to procedure definitions.

Still open:

1. **The default run-folder path template.** (open) Proposed:
   `{date}/{procedure}_{device}_{time}`. Ask the people who will browse it.
2. **Migrations.** (open) Proposed: the version check alone until a real change
   needs a migration; then Alembic, not a hand-rolled substitute.
3. **Fill-down in the viewer.** (deferred) Explained here because it is easy to
   misread. When a reading is taken at an outer loop level, for example the
   device voltage read once per bias before a trigger sweep, it lands on its own
   row:

   | seq | bias_voltage | trigger_mV | count_rate | device_voltage |
   |---|---|---|---|---|
   | 0 | 0.02 | | | 0.0 |
   | 1 | 0.02 | −50 | 33333.3 | |
   | 2 | 0.02 | −40 | 40000.0 | |

   Plotting `count_rate` against `device_voltage` then draws nothing, because no
   row has both. Fill-down would copy row 0's voltage onto rows 1 and 2 when
   plotting, on the assumption that it did not change during those counts. That
   assumption is an analysis choice, so it would happen at read time only,
   never in storage. No current procedure reads at an outer level, so nothing
   needs it yet. It is one polars expression,
   `pl.col("device_voltage").forward_fill().over("run_id", "bias_voltage")`,
   which passes D9 if it is ever wanted.

---

## 15. Traps

- **Close the open row on every exit path.** A missed close loses the last
  point.
- **`latest` updates on `observe()`, not on close.**
- **`values` is an SQL keyword.** The `points."values"` column must be quoted
  in every query.
- **NaN and infinity are not JSON.** `json.dumps` writes `NaN` by default, which
  SQLite's JSON functions reject. Store `null`; use `allow_nan=False` so a
  regression fails loudly. In CSV, write an empty cell.
- **Relative paths resolve against the process's working directory**, not the
  project. This is today's `db_path` bug; every path the recorder and file saver
  use must be resolved from the workspace or project directory explicitly.
- **Local time for `date` and `time`** facets and folder names; UTC in storage.
- **Facet volume.** Keep only scalar leaves; cap value length.
- **Float facets.** Store a canonical `repr`.
- **Two runs writing at once.** Each run process has its own context and
  connection; WAL mode serializes writes. No process-global buffer.
- **The file saver is not the record.** If it fails (disk full, a path the
  template makes invalid) it logs and stops writing files; it must never fail
  the run, because the database has the data.

---

## 16. Pointers

- [`runner_plan.md`](runner_plan.md): running from the GUI, the event stream
  over a websocket, and both plotters.
- [`procedure_plan.md`](procedure_plan.md) §2.6: "plotting is column
  selection", which this keeps.
- [`database_plan.md`](database_plan.md): the original schema and its
  reasoning.
- [`server_plan.md`](server_plan.md) Phase 9: why two runs can record at once.
- `procedure_framework/lab_procedure/`: Phase 1.
- `lab_wizard/wizard/workspace.py`, `lab_wizard/lib/client/server_discovery.py`:
  the two workspace discoveries (§3).
- `lab_wizard/lib/task_adapters/provenance.py`: the instrument snapshot.
- `lab_wizard/wizard/frontend/src/routes/data/database/`: the stub the viewer
  replaces.
