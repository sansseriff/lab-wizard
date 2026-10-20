# Setups: what the experiment was, recorded with every run

> **Status: Phases 1–4 built (2026-10-01).** Corrections (5), history (6) and
> the stretch goal are not. Where the build differs from the text below, §11
> says how. Decisions marked **(open)** in §10 can wait until the phase that
> needs them.
>
> **This is a startup document**, written for an engineer or agent picking this
> up cold. It builds on [`semantic_data_plan.md`](semantic_data_plan.md) (the
> lab database, facets, the Data page, derived columns) and replaces the
> free-form `run.metadata` editor on the Run page. As with that plan, no
> backwards compatibility with existing databases is required.

---

## 1. The problem

A run's data only means something next to the state of the apparatus it was
taken on: which detector was mounted, which bias resistor was in the line,
which balun, what the cryoamp was biased at, how much QCL power. Today that is
spread over three places:

| What | Where it lives now | What goes wrong |
|---|---|---|
| The device under test | `run.device`, a name in the `devices` table, with properties (`wafer`) | Fine. It is the only part of the apparatus with a registry. |
| Instrument settings | a snapshot of each bound instrument's params, taken at run start | Fine, and automatic, but it covers only what an instrument *is set to*, not what is wired to it. |
| Everything else | `run.metadata` in **each project's** YAML, or a procedure param | Typed again in every project; drifts between projects; typos become new filter values; a blank or stale field goes unnoticed. |

The worst case is a value that scales the data. `iv_curve` infers current
through `readout.bias_resistance_ohm`, a procedure param with a default of
100 kΩ. Forget to change it after swapping in a 10 kΩ resistor and every IV
curve for a week is off by ten, silently.

## 2. Where a value lives

Values differ by **who changes them, and how**. That decides where each one
lives:

| Kind | Changed by | Lives in | Recorded with each run as |
|---|---|---|---|
| Connection (address, port) | nobody, per run | `config/instruments` | nothing; instruments are referred to by `attribute_name` |
| Instrument state (scope at 50 Ω, a laser's power set over the bus) | software, outside the measurement | the instrument's baseline in `config/instruments` | the instrument snapshot (exists today) |
| **Setup facts** (bias resistor, balun, cryoamp bias, QCL power set at a knob) | a person, with hands | **the setup** (this plan) | a copy of the setup's fields |
| Procedure params (the sweep, the settle time) | the procedure, during the run | the project | `params` |

Something can move between rows. A laser whose power the procedure sweeps is a
procedure param. The same laser left at a fixed power by its driver is
instrument state. Set at a front-panel knob, its power is a setup fact. The
test is always whether the software sets it, and when.

The device is not in this table. It has its own record (§3).

## 3. Concepts

**Setup**: a named, long-lived description of one experiment, such as
`cryostat-A` or `optics-bench`. It holds the experiment's **current** setup
facts as free-form fields, plus the device currently mounted in it. A lab may
have several setups on one computer, for different people, benches or
measurements. Two setups may share an instrument (one voltage source moved
between them). The instrument stays one workspace-level record, and leases
decide who holds it.

**Sticky values**: a setup's fields are what was true last time. Opening the
wizard next week starts from them. Changing the one value that changed is a
quick edit, not a re-entry of everything.

**Snapshot**: when a run starts, it records the setup's name, a **copy of its
fields**, and the mounted device. The copy is what keeps a run
self-describing. Editing the setup later never changes a past run. Plots of
last month's runs stay as they were drawn last month, even if the setup is
renamed or deleted.

**History is read from runs.** Setups have no revision table. Diffing the
copies of consecutive runs on a setup gives its timeline, for example
"bias_resistor 100 kΩ → 10 kΩ between run 41 (Tue) and run 42 (Thu)". A
change that no run saw affected no data.

**Correction**: changing a field in past runs' copies, deliberately, from the
Data page ("those were 10 kΩ, not 100 kΩ"). Each correction is logged with the
old value, and derived columns are computed when data is read, so the
affected plots rescale at once. This is the only way a snapshot changes. A
live link from runs to the setup would rescale old plots whenever someone
edited the setup today; a correction does so only on purpose.

**Device and setup change independently.** Sometimes the bench stays the same
and the SNSPD is swapped. Other times the resistor, balun or cryoamp bias
changes and the device stays. So the device stays its own record, with its
intrinsic properties (wafer, area), which go with it from setup to setup. The
setup only *remembers which device is mounted*, as a sticky default like any
other field. It refers to the device and copies none of its properties.

## 4. Procedures that need setup facts

A procedure does not know which bench it will run on. A measurement does: it
is the procedure made specific, by name, in the projects folder. So setup
facts work the way instruments already do:

| | The procedure declares | Creating a measurement binds it to |
|---|---|---|
| Instruments | a **role**: `voltage_source: VSource` | an instrument in `config/instruments` |
| Setup facts | a **need**: `bias_resistance: ohm` | a field of the chosen setup: `channel2.bias_resistor` |

The procedure declares its needs beside its roles, and its derived columns
read them by the need's name:

```yaml
roles:
  voltage_source: {behavior: VSource, description: Biases the detector through the bias resistor.}
  voltage_sense:  {behavior: VSense,  description: Reads the voltage across the detector.}
needs:
  bias_resistance: {unit: ohm, description: the resistor the current is inferred through}
derived:
  current: (bias_voltage - sense_voltage) / setup("bias_resistance")
```

- **`setup("name")`** joins `param("path")` in the expression language
  (`lib/data/expressions.py`). `name` is one of the procedure's needs, never
  a field path, so the procedure knows nothing about any one setup's layout.
  It returns the bound field's value from each run's own copy, converted to
  the need's unit: `{value: 100, unit: kΩ}` reads as `100000.0` for
  `unit: ohm`. Conversion covers SI prefixes on the same base unit; any other
  unit is an error naming the field.
- **In the procedure editor** (the Plots tab's *Setup needs* panel), using
  `setup("x")` in a derived column for an undeclared `x` offers to declare
  it. An unused need is not flagged, as an unused role is not.
- **The setup is not typed against procedures.** It stays free-form fields.
  A need with no field bound, or bound to a field that is missing or not a
  number in that unit, stops a run before it starts (§7). A need has no
  default, because a default is exactly what made the 100 kΩ mistake silent.
- Two setups that spell a field differently (`bias_resistor` here, `r_bias`
  there) are no problem: each measurement binds the need to its own setup's
  field.
- `iv_curve` drops `readout.bias_resistance_ohm` from `params` and declares
  the need `bias_resistance`. A param that no step reads is the sign of a
  setup fact in the wrong place.
- Custom (Python) measurements have no definition to declare needs in. They
  read the run's setup copy themselves, if at all.

## 5. The data model

In the lab database, beside `devices`:

```sql
CREATE TABLE setups (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    notes      TEXT,
    fields     TEXT NOT NULL DEFAULT '{}',   -- JSON: the current facts
    device_id  INTEGER REFERENCES devices(id) -- what is mounted now
);

CREATE TABLE run_corrections (
    id       INTEGER PRIMARY KEY,
    run_id   INTEGER NOT NULL REFERENCES runs(id),
    field    TEXT NOT NULL,     -- "bias_resistor", or "channel2.bias_resistor"
    old      TEXT,              -- JSON; null if the field was missing
    new      TEXT,              -- JSON; null removes it
    at       TEXT NOT NULL,
    author   TEXT,
    note     TEXT NOT NULL      -- "was the 10 kΩ since Tuesday's swap"
);

-- runs gains:
--   setup         TEXT   -- the setup's name, as it was
--   setup_fields  TEXT   -- JSON copy of its fields at run start, corrections applied
--   setup_needs   TEXT   -- JSON: the measurement's bindings, {"bias_resistance": "channel2.bias_resistor"}
-- runs loses:
--   metadata
```

One correction made on twelve runs is twelve rows sharing `at` and `note`,
shown as one entry. Undoing a correction is another correction.

Quantities are `{value, unit}`, as `run.metadata` uses now.

**Pictures** are field values too: `{image: "3f9a…c1.jpg"}`, a reference to
a file in `<data_dir>/setup_images/` (beside `lab.db`), named by the SHA-256
of its contents. A field may hold one image or a list (`wiring_photos`).
Because a picture is a field, it is snapshotted with the run, shows in the
timeline and in "what changed" ("wiring_photo changed", with both
thumbnails), and can be corrected like any value. Files are written once and
never changed. A file is deleted only when no setup and no run's copy refers to
it, which a cleanup command checks; nothing deletes automatically. Images are
not facets.

A run records its bindings with its copy, so reading `setup("bias_resistance")`
for a past run needs neither the project nor the setup: the run's
`setup_needs` names the field, and its `setup_fields` holds the value. A
correction changes the value in the copy, so it reaches every derived column
bound to that field.

**Where the project says it.** The project YAML gains a `setup:` block beside
`roles:`, written when the measurement is created:

```yaml
run:
  operator: null
  notes: null
setup:
  name: mid-ir-bench
  needs:                     # which of the setup's fields fills each need
    bias_resistance: channel2.bias_resistor
roles:
  voltage_source: yoko_ch1
  voltage_sense: keithley
```

`run:` loses `metadata:` and `device:`. The device comes from the setup's
mounted device, so there is one place that says what is in the cryostat. A
run started from a terminal resolves the setup and its needs the same way,
because it records into the same database. `RunStarted` carries the setup
name, the copy of its fields, the bindings and the device.

## 6. Filtering by anything in a setup

Every leaf of a run's `setup_fields` becomes a facet under `setup.`
(`setup.bias_resistor`, `setup.qcl.power`, `setup.balun`). Facets of a run's
`metadata` work this way today under `run.`, so this is mostly a rename:

- `facets.run_facets` flattens `setup_fields` under `setup.` in place of
  `metadata` under `run.`, and adds `setup` (the name) as a facet. `device`
  and `device.*` are unchanged.
- `data_api.FACET_GROUPS` gets a **Setup** section (`setup`, `setup.`) in
  place of **Run**, placed after Device.
- A numeric field with many values gets the range filter already used for
  numeric facets. Text fields (balun model) get the value checkboxes.
- **Units.** A facet's `num` is converted to the field's base unit, so a
  range over `bias_resistor` compares 100 kΩ and 100000 Ω correctly, and both
  show as one value. This uses the same prefix conversion as `setup()`.
- A correction rewrites the facets of the runs it touched
  (`write_run_facets`), so filters show corrected values.

Filtering by device, setup and procedure params at the same time takes no new
query. It is the existing AND of facet filters.

## 7. The pages

**Setup page** (a new top-level section, beside Instruments):

- A list of setups, with a button to create one.
- Each setup opens to its current fields as a form (free-form, as the
  metadata editor is now, plus an image field that takes a dropped or chosen
  file, or several), its mounted device (the device combobox), and its
  **timeline**: the changes between consecutive runs, newest first, each
  linking to its runs on the Data page, with corrections marked.

**Create measurement** (the Select resources page, which binds roles to
instruments today) gains a **Setup** section beside Instruments:

- a setup picker (a combobox of setups, or create one by name);
- then one row per need: its name, unit and description, and a combobox that
  searches **that setup's** fields. Typing `bias` lists `channel2.bias_resistor`
  and `cryoamp.bias_v` with their current values; fields in the need's unit
  come first, and fields in another dimension are shown but cannot be chosen.
  A need with an exact name match is bound already;
- if no field fits, **Add to setup**: name the field and give its value, and
  it is written to the setup and bound;
- the page asks for every need to be bound before it creates the
  measurement, as every role must be bound to an instrument. The backend only
  checks the bindings it is given, so a project can be made with a need left
  for the Run page; its runs do not start until it is bound (§11).

**Run page:** the metadata editor goes. In its place:

- the setup's name and **the bound fields first**, with their values, editable
  in place. Editing one writes to the setup, because the bench changed, not
  just this run. A bound field that has gone missing blocks Start and is asked
  for here;
- the mounted device, likewise editable, which writes to the setup;
- the setup's other fields, folded away, read-only here;
- changing the setup re-runs the binding for the new setup, with exact
  name matches bound already.

**Data page:**

- the **Setup** facet section (§6);
- in a plot's derived columns, `setup("…")` reads the needs of the selected
  runs' procedures (offering them as you type is not built);
- in a run's details, its setup name, its copy of the fields, and any
  corrections made to it;
- for two selected runs, **what changed**: their setup copies, devices and
  instrument snapshots, diffed;
- **Correct setup field**, on a selection of runs: choose a field, give the
  right value and a note. It shows the current values it will replace, writes
  the corrections, and offers to set the setup's current value too.

## 8. Phases

1. **Record**: the `setups` table; `setup`/`setup_fields`/`setup_needs` on
   runs; the `setup:` block in the project YAML; `run.metadata` and the
   project's `device:` removed; `setup.*` facets and the Setup sidebar
   section. Schema version 2.
2. **Setup page**: list, create, edit fields, mounted device, pictures.
3. **Needs**: `needs:` in procedure definitions, `setup()` in the expression
   language with unit conversion, the Setup section on Create measurement
   with its field combobox, `iv_curve` ported.
4. **Run page**: the bound fields in place, refuse to start without them.
5. **Corrections**: `run_corrections`, the Data page action, facets
   rewritten.
6. **History**: the timeline on the Setup page, and what changed between two
   runs on the Data page.

Phases 1 and 2 are the minimum worth shipping. Phases 3 to 5 are what fixes
the wrong resistor.

**Stretch goal, after phase 6: changed since this measurement last ran.** The
Run page compares the setup's current fields with the copy recorded by this
project's latest run, and shows what differs as an information line:
"bias_resistor changed 100 kΩ → 10 kΩ since this measurement last ran
(Tuesday)". There is nothing to accept or sync: the project YAML refers to
the setup by name, and the next run records the current values regardless.
It reuses phase 6's diff of two copies.

## 9. What it is not

- Not instrument configuration. Addresses and baselines stay in
  `config/instruments`, and a run's instrument settings are still
  snapshotted automatically.
- Not measurement params. The sweep and the gate time stay in the project.
- Not the device registry. Devices keep their own record and properties.
- Not an inventory system. It records what is installed, not what is on the
  shelf.
- Not a link. A run never reads its setup again after it starts; it reads its
  copy.

## 10. Open questions

1. **(open) The name.** "Configuration" collides with `config/`. This
   document says *setup*. Alternatives: *Bench*, *Experiment*, *Apparatus*.
2. **(open) Log edits to the setup itself?** The timeline is read from runs,
   so an edit is visible once a run uses it, but a reason for the change
   ("swapped R_bias after the open circuit") can only go in that run's notes.
   A small `setup_changes` log would hold reasons and edits that no run saw.
   Suggestion: wait until notes prove too weak.
3. **(open) A field template.** Free-form fields let typos become new facet
   values (`bias_resistor` vs `bias_resistance`) within a setup. Bindings
   already absorb spelling differences between setups (§4), so a lab-wide
   list of known fields is only about filtering. Offering existing paths as
   you type a new field may be enough.
4. **(open) Per-run extras.** With `metadata` gone, is anything per-run still
   needed besides `notes`? A per-run temperature reading belongs in the data,
   recorded by a step.

## 11. As built (phases 1–4)

Where the code is, and where it differs from the text above.

- **Data:** `lib/data/setups.py` (fields, needs, the `setups` table,
  pictures), `lib/data/units.py` (SI prefixes; mirrored for the page in
  `frontend/src/lib/setups/units.ts`), schema version 2 in `lib/data/schema.py`.
  A version 1 `lab.db` is refused with a message to move it aside.
- **Runs:** `RunStarted` carries `setup`, `setup_fields` and `setup_needs`
  in place of `metadata`. `lib/task_adapters/run.py` `resolve_setup` copies
  the setup and checks the procedure's needs when a run starts; the Run page's
  launch checks the same first, so the refusal shows there instead of after
  instruments are claimed. `record_run(setup=...)` does the same for a script.
- **Generation is lenient about unbound needs.** Outside a workspace a
  project records into its own database, which does not exist before the
  project does, so a backend that insisted on every need being bound could not
  create one there. It checks the bindings it is given (the setup exists, each
  bound field reads in its need's unit) and leaves the rest to the run.
- **Old projects:** a `run:` block's `device:` and `metadata:` are ignored,
  not refused. Such a project records no device until it names a setup with
  one mounted.
- **Pages:** Setups (`routes/setups`), the Setup section on Select resources
  and on the Run page (`lib/setups/NeedBindings.svelte`, shared), the Setup
  facets and run details on the Data page. Run page edits to bound fields and
  the mounted device are a draft, saved to the setup when the project is
  saved.
- **Naming:** "setup" now means two things in the code: this, and a project's
  generated `*_setup.py` file. §10.1 is still open.
