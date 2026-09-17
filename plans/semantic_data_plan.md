# Saving what a measurement *means*

> **Status: proposed — decisions needed, nothing built.**
>
> **This is a startup document.** It is written for an engineer or agent picking
> this up cold, and it is meant to be enough on its own: the problem, the code
> as it stands, what other people do, a proposal, the decisions that are not
> mine to make, and the traps. Read it end to end before changing anything.
>
> It follows from [`procedure_plan.md`](procedure_plan.md) meeting
> [`database_plan.md`](database_plan.md). Now that a procedure is a declared
> tree rather than a hand-written loop, the saving system can finally know which
> of a run's values are *the measurement* and which are *the circumstances*.

---

## 1. The problem, in the lab's words

An MCR curve **means** one thing: count rate as a function of optical
attenuation. Everything else it records — the bias it held, the phase labels,
the device voltage, the order the points were taken in — matters for judging
drift and hysteresis, but is not what the measurement *is*.

A PCR curve sweeping bias voltage at several trigger levels means count rate as
a function of (bias, trigger level). Whether the run swept all biases at one
trigger level and then repeated, or alternated the loops the other way round, is
**operationally** different — it changes what drifts between neighbouring points
— but **semantically** identical. Both runs measure the same surface.

So the stored form has to carry two things at once:

1. **the relation** — which columns are axes, which are readings;
2. **the run as it happened** — order, nesting, phases — so hysteresis and drift
   remain visible.

Today the second is recorded and the first is not recorded at all. Nothing in
the database says that `attenuation_db` is an axis and `counts` is a reading;
every consumer — a plot, an export, a person six months later — guesses from
column names.

**The goal is not to throw away the hierarchy.** It is to stop the hierarchy
being the only thing the data model knows.

---

## 2. What exists today

### 2.1 The pipeline, end to end

| Stage | Where |
|---|---|
| A procedure definition (roles, params, step tree) | `config/procedures/<name>.yml`, or `lab_wizard/lib/procedures/library/` |
| Generated Python for a project | `lab_wizard/lib/procedures/codegen.py` → `projects/<project>/<name>.py` |
| Steps that touch instruments | `lab_wizard/lib/task_adapters/instrument_steps.py` |
| The run loop and messages | `procedure_framework/lab_procedure/` (`core.py`, `steps.py`, `context.py`, `messages.py`) |
| Message → saver bridge | `lab_wizard/lib/task_adapters/savers.py` (`SaverSink`) |
| Message → plotter bridge | `lab_wizard/lib/task_adapters/plotters.py` (`PlotterSink`) |
| The database | `lab_wizard/lib/savers/schema.py`, `database_saver.py`, `query.py` |

### 2.2 How a row is produced now

`RunContext.observe(data)` (`procedure_framework/lab_procedure/context.py`) is
the only way data leaves a step:

```python
snapshot = self.snapshot_parameters()          # every swept/bound parameter in force
observation = Observation(
    data={**snapshot, **data},                 # flat: axes and readings together
    metadata=snapshot,
    sequence_index=self.next_sequence_index(),
    sweep_index=self.sweep_index,
)
self.latest.update(data)                       # what condition steps read
self.data_bus.emit(observation)
```

Parameters come into force through `Sweep` (one per value) and `WithParameter`
(a fixed label such as `phase: background`), both via
`RunContext.bound_parameter`, which is scoped — it restores the previous value
on the way out.

`SaverSink.handle` then pulls `counts`, `int_time`, `delta_time` and
`temperature` out of `data` into typed columns and passes the whole dict as
`data`, and `DatabaseSaver.write_measurement` writes one `measurements` row.

### 2.3 What that actually produces

A real `mcr_curve` run over three attenuations, dumped from SQLite
(`runs` is one row; `measurements` is seven):

```
counts=1     data={"phase":"background","counts":1,"int_time":0.05,"count_rate":20.0}
counts=387   data={"phase":"signal","attenuation_db":20.0,"counts":387,"int_time":0.05,"count_rate":7740.0}
counts=NULL  data={"phase":"signal","attenuation_db":20.0,"device_voltage":0.0}
counts=4004  data={"phase":"signal","attenuation_db":10.0,"counts":4004,...}
counts=NULL  data={"phase":"signal","attenuation_db":10.0,"device_voltage":0.0}
counts=39165 data={"phase":"signal","attenuation_db":0.0,"counts":39165,...}
counts=NULL  data={"phase":"signal","attenuation_db":0.0,"device_voltage":0.0}
```

Note what is right and what is wrong:

- **Right:** the axis value (`attenuation_db`) is copied onto every row, so the
  loop nesting is already invisible in the data. That is the property the lab
  wants, and it already holds.
- **Wrong:** one point of the curve is **two rows** — counts in one,
  `device_voltage` in the other, `counts` NULL — reassemblable only by matching
  on `attenuation_db`. `database_plan.md` says *one row = one integration*;
  practice drifted to *one row per `observe()` call*.
- **Missing:** nothing says `attenuation_db` is the axis.

### 2.4 The schema as built

Six tables: `wafers → devices → runs → measurements → measurement_details`,
with `cryostats` beside `runs` (`lab_wizard/lib/savers/schema.py`).

```
runs         id, cryostat_id, device_id, run_type, started_at, ended_at,
             operator, description, config (JSON), instruments (JSON)
measurements id, run_id, timestamp, counts, int_time, delta_time,
             temperature, data (JSON), metadata (JSON)
```

**A procedure adds no columns.** Everything it records lands in
`measurements.data`. That is worth stating plainly because it is often assumed
otherwise: the schema is fixed; procedures add *keys*.

Three further gaps, all independent of the semantic question:

- **`runs.run_type` is a five-value enum** (`PCR_CURVE`, `IV_CURVE`,
  `MCR_CURVE`, `EXTENDED_PCR`, `OTHER`) in a `VARCHAR(12)` column.
  `_coerce_run_type` turns anything else into `OTHER`, so every composed
  procedure — `dark_counts`, and anything a user builds in the composer — is
  stored indistinguishably. A longer name would not fit the column width
  either.
- **`runs.device_id` is always NULL.** The project YAML carries a `run.device`
  block (name, model, description) and a `run.metadata` block (operator,
  description, tags), and **nothing reads them**: neither `iv_curve.py`,
  `pcr_curve.py` nor `codegen.py` passes `device=` to `RunStarted`. For a
  database whose whole hierarchy is wafers → devices → runs, every run is
  unattached.
- **`measurement_details`** (`detail_type`, `bin_index`, `bin_value`, `value`)
  exists for per-point arrays and has never been written to.

`runs.instruments` was added recently (procedure plan 5.6) and holds what each
instrument was configured with at run start. `schema.add_missing_columns` adds
nullable columns to databases written before they existed — there is no
migration framework, and this is the substitute.

---

## 3. How other people solve this

None of this is exotic; it is the oldest problem in experiment data.

| System | Where the structure lives | Storage shape |
|---|---|---|
| **QCoDeS** (SQLite; common in quantum-device labs) | an explicit *interdependency graph* per run: each parameter is declared dependent, independent, or inferred-from | a results table per run, one real column per parameter |
| **xarray / netCDF**, and Quantify on top of it | `dims`/`coords` versus `data_vars` — coordinates are declared, not guessed | N-dimensional arrays, gridded |
| **NeXus / HDF5** (synchrotrons, neutron sources) | `axes` and `signal` attributes on the data group | arrays in a file hierarchy |
| **Bluesky / databroker** (NSLS-II and others) | event *descriptors* naming each stream's fields and shapes | documents in a metadata store, bulk arrays in external files |
| **Tidy data / star schema** (Wickham; Kimball) | convention: a row is an observation; setpoints and readings are columns; context lives in dimension tables | one fact table plus joined dimensions |
| **EAV** (entity–attribute–value) | nothing is declared; every number is a row | maximal flexibility, poor ergonomics — an anti-pattern for primary data |

*(Details of QCoDeS's table-per-run layout are from memory and worth checking
against its docs before copying anything.)*

**The consensus is not about storage — it is about declaring the structure.**
Every mature system stores, alongside the numbers, a statement of which
quantities are axes and which are readings. None encodes loop nesting as
structure; loop order is at most an ordinary recorded value. That is exactly the
"operationally different, semantically the same" property wanted here, and it is
why a QCoDeS dataset can be plotted or exported to xarray without anyone knowing
how its loops were written.

Where systems genuinely differ — where the trade-offs are real — is the
substrate:

| Substrate | Good | Bad |
|---|---|---|
| **A column per parameter, a table per run** (QCoDeS) | fast, typed, obvious within a run | DDL at run time; cross-run queries visit many tables; a table per run |
| **One long table + JSON per row** (what we have) | no DDL ever; ragged and sparse runs are free; cross-run queries hit one table | values need `json_extract`; no types; indexing needs generated columns |
| **Value rows** (EAV) | any shape at all | every query is a pivot; slow; unreadable by hand |
| **Catalog + array files** (HDF5/Parquet beside a DB) | scales to large arrays; native to analysis tools | two stores to keep consistent; incremental durability needs care |

There is **no de facto winner on substrate**. There *is* a de facto winner on
the question being asked here: store the dependency structure explicitly, and
keep execution order as data rather than as shape.

---

## 4. The advantage lab_wizard has

QCoDeS asks the user to declare `register_parameter(..., setpoints=...)`.
**Here, the procedure already knows.** Every fact needed for the graph is in the
definition:

| In the definition | Role in the data |
|---|---|
| `sweep.parameter` | **coordinate** — an axis of the relation |
| a step's `emits` (`count` → `counts`, `int_time`, `count_rate`; `read_voltage` → its `field`) | **measured** — a reading |
| `with_parameter.parameter` | **context** — labels a portion of a run (`phase`) |
| params referenced but not swept (`bias.voltage`) | **setting** — constant for the run, already in `runs.config` |

For `mcr_curve` that derives exactly: coordinate `attenuation_db`; measured
`counts`, `int_time`, `count_rate`, `device_voltage`; context `phase`. Which is
precisely "count rate against attenuation, with a background phase".

`StepParams.emits` and `swept_parameters()` already expose this
(`lab_wizard/lib/procedures/spec.py`), and `ProcedureDefinition.emitted_fields()`
already walks the tree collecting both — the composer's "Records" panel is built
from it.

So the answer to *"how do we let the user select the semantic data?"* is: **do
not ask by default.** Derive it, show it in the composer, and let it be
overridden where the inference is wrong. A question with an obvious answer 95%
of the time is a question people learn to click through without reading.

---

## 5. Proposal

Five changes. The first three are the semantic model; the last two are
independent gaps that belong in the same pass because they touch the same rows.

### 5.1 Derive an observation schema per procedure

`{name: (role, unit, source_step)}` for every column a procedure can produce,
where role ∈ `coordinate | measured | context`. Derived from the definition;
overridable per field. Surface it in the composer's Records panel, which today
lists column names with no roles.

### 5.2 Store it with the run

A `run_parameters` table (`run_id`, `name`, `role`, `unit`, `source_step`), so a
run stays self-describing after the procedure is edited or deleted. This is
QCoDeS's interdependency graph, derived instead of hand-declared.

Hand-written measurements (`iv_curve`, `pcr_curve`) have no definition to derive
from, so they need either a small declaration in their module or no entry at
all — decide, don't leave it implicit.

### 5.3 One row per point

Accumulate readings into the current point and flush one row per point. A point
ends when:

- the innermost enclosing loop iteration ends, **or**
- a field would be overwritten (a `repeat` of 10 counts is 10 points), **or**
- an explicit `record` step says so, for full control.

Keep `sequence_index`, sweep indices and `phase` on the row: execution order
stays recoverable, it just stops being the structure.

The natural home is `RunContext` (it already holds `parameters`, `latest` and
the sequence counter) with the flush driven by `Sweep`/`Repeat` scope exit. Be
careful: `RunContext.latest` feeds condition steps (`ValueAbove`/`ValueBelow`)
and must keep updating *immediately*, not at flush time, or a procedure that
branches on what it just measured will read stale values.

### 5.4 `runs.run_type` becomes a plain string

Holding the procedure name. The enum's only benefit is a typo check, and it is
losing exactly the information the procedure system exists to organize.
`query.py::get_runs(run_type=...)` filters on it and keeps working with strings.
Existing rows hold enum *names* (`MCR_CURVE`), so decide whether to migrate
them to lowercase names or accept both on read.

### 5.5 Fill in the device

Pass `device`, `cryostat`, `operator` and `description` from the project YAML's
`run:` block into `RunStarted`, in `codegen.py` and both hand-written
measurements. `DatabaseSaver._resolve_device_id` already resolves or creates a
device row; nothing calls it with anything. This is the largest single gap in
the database and the cheapest to close.

---

## 6. Decisions needed (not the implementer's to make)

1. **Does a point ever span an outer loop?** The rule in 5.3 ends a point at the
   innermost loop. A procedure that counts once per outer iteration and reads a
   temperature once per inner one would produce points with holes. Is that a
   real pattern in this lab?
2. **Do role overrides belong to the procedure or to the project?** Per
   procedure is simplest. A project wanting a different reading of the same
   procedure would need its own override.
3. **Are old rows migrated?** Existing databases have one-row-per-observation
   data and enum run types. Convert, or leave old runs as they are and change
   only new writes? (There is no migration framework; `add_missing_columns` is
   additive only.)
4. **Do per-point arrays** (a histogram, a trace) go in `measurement_details`,
   in JSON, or in files beside the database? Not urgent until something records
   one, but it decides whether `measurement_details` survives.
5. **Should the composer show roles read-only, or make them editable?** Editable
   costs a UI and a storage field; read-only may be enough for a year.

---

## 7. Traps

- **`WithParameter` is not a sweep.** `phase: background` is a label, not an
  axis; treating every bound parameter as a coordinate would make `phase` an
  axis of the relation. The distinction exists in the definition (`sweep` vs
  `with_parameter`) — keep it.
- **Sweeps nest and reuse names.** `RenderContext.scoped` uniquifies Python
  identifiers when two nested sweeps bind the same parameter name; the data side
  has no such protection.
- **`Retry` re-runs a failed child.** A retried `count` emits twice for one
  point; the overwrite rule would split it into two points. Probably wrong —
  decide whether a retry replaces the value.
- **Conditionals skip readings.** `If`/`Selector` mean some points legitimately
  lack a field. Rows must stay ragged; do not assume a rectangular grid.
- **Plotters consume the same stream.** `PlotterSink` forwards every
  `Observation.data` to `GenericPlotter.plot`. Changing row granularity changes
  what plotters see — coordinate the change, or plots quietly halve their points.
- **Two runs can interleave on one server.** Claims allow two runs to hold
  different channels of one counter simultaneously (`server_plan.md` Phase 9).
  Each run has its own `RunContext`, so accumulation is per run — but do not
  introduce any process-global point buffer.
- **`counts` is duplicated** in both a typed column and `data`. Whatever the new
  shape is, keep exactly one of them authoritative.
- **Legacy projects still run.** Projects generated months ago carry their own
  instrument copy and use hash lookup; they must keep writing valid rows.

---

## 8. How to test it

The repo's testing style is end-to-end against a **simulated rack** — real
drivers, no hardware — and assertions against the detector model's own physics.
Follow it rather than mocking savers.

| Use | File |
|---|---|
| A procedure run end to end, data checked against the physics | `tests/test_mcr_curve.py` |
| A generated project run as a script, with a database saver, rows read back from SQLite | `tests/test_own_server_projects.py` |
| Saver/observation unit behaviour | `tests/test_lab_wizard_task_adapters.py` |
| Definitions, catalogs, codegen | `tests/test_procedure_definitions.py`, `tests/test_procedure_projects.py` |
| Schema migration in place | `tests/test_provenance.py::test_a_database_written_before_the_column_existed_still_opens` |

Worth writing: a procedure with **two nested sweeps** whose loop order is
swapped between two runs, asserting both produce the *same relation* (same set
of (x1, x2, y) tuples) and different `sequence_index` orders. That is the
property this whole document is about, and nothing tests it today.

---

## 9. Pointers

- [`procedure_plan.md`](procedure_plan.md) — the procedure system, its
  decisions, and the parameter-tier model (§2.1).
- [`database_plan.md`](database_plan.md) — the original schema rationale. Its
  §"The Core Data Model" already describes one row per integration carrying
  every parameter in force; this document is largely about honouring it.
- [`server_plan.md`](server_plan.md) Phase 9 — claims, and why two runs can
  write to one database concurrently.
- `docs/concepts/procedures.md` — the user-facing description of steps,
  observations and presets.
