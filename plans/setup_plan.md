# The apparatus record: what the experiment was, dated

> **Status: Proposed (2026-09-29).** Nothing is built. Decisions marked
> **(open)** in §9 need an answer before Phase 1.
>
> **This is a startup document**, written for an engineer or agent picking this
> up cold. It builds on [`semantic_data_plan.md`](semantic_data_plan.md) (the
> lab database, facets, the Data page) and replaces the free-form `run.metadata`
> editor on the Run page. As with that plan, no backwards compatibility with
> existing databases is required.

---

## 1. The problem

A run's data only means something next to the state of the apparatus it was
taken on: which detector was mounted, which bias resistor was in the line,
which laser, which shunt, which fibre and attenuator chain, which cryostat and
what it was cooled to. Today a run captures that in three unrelated places:

| What | Where it lives now | What goes wrong |
|---|---|---|
| The device under test | `run.device`, a name in the `devices` table, with properties (`wafer`) | Fine for the device itself. It is the only part of the apparatus with a registry. |
| Instrument settings | a snapshot of each bound instrument's params, taken when the run starts | Fine, and automatic — but it only covers what an instrument *is set to*, not what is wired to it. |
| Everything else | `run.metadata` in **each project's** YAML, typed on the Run page | Typed again in every project; drifts between projects; typos become new filter values; nothing records *when* it changed; a project made last month still says what was true last month. |

The third row is most of the apparatus, and it changes all the time, in small
steps: a resistor swapped on Tuesday, a new laser on Thursday. What a lab
needs is closer to a **lab notebook for the apparatus** than to per-run fields:
one record of the setup, changed in small dated edits, and every run linked to
the version that was true when it ran.

## 2. What it should let a lab member do

1. **Describe the apparatus once**, as a structured record: fields with units
   where they have them (`bias_resistor: 100 kΩ`, `laser: {model, wavelength: 1550 nm}`).
2. **Change it in small, dated steps**, each with a note ("swapped R_bias after
   the open-circuit on channel 2"), and see the history as a timeline.
3. **Have every run record which version it ran on**, without typing
   anything at run time.
4. **Filter and compare by it** on the Data page: every run with the 100 kΩ
   resistor; *what changed between these two runs?*
5. **Keep several**, when a lab has more than one bench or cryostat.

## 3. Concepts

**Setup** — a named record of one apparatus: `cryostat-A`, `optics-bench`. It
holds the lab-specific facts about the experiment that are *not* needed to
connect to an instrument or to scale a plot. (Connection details stay in
`config/instruments`; a run's measurement params stay in its project.)

**Revision** — one version of a setup, immutable once saved: its fields, when
it took effect, who made it, and a note. Editing a setup always adds a
revision; nothing is overwritten. A setup's *current* revision is its latest.

**Link** — a project names the setup it runs on (by name, not by revision).
When a run starts it resolves that name to the current revision and records
both the revision's id **and a copy of its fields** with the run. The copy is
what keeps a run self-describing, the rule the Data page now follows for plots:
everything needed to understand a past measurement comes from the measurement
itself, even if the setup is later deleted or its history corrected.

**Fields template (open, §9)** — optionally, a lab-wide list of the fields a
setup has, each with a type, a unit, and for some a list of allowed values
(the lab's lasers). A template is what turns "a typo becomes a new filter
value" into "a typo is refused", the same move the device combobox made for
device names.

## 4. The data model

In the lab database, beside `devices`:

```sql
CREATE TABLE setups (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE,
    notes TEXT
);

CREATE TABLE setup_revisions (
    id           INTEGER PRIMARY KEY,
    setup_id     INTEGER NOT NULL REFERENCES setups(id),
    effective_at TEXT NOT NULL,     -- when the apparatus changed
    recorded_at  TEXT NOT NULL,     -- when someone wrote it down
    author       TEXT,
    note         TEXT,              -- "swapped R_bias after the open circuit"
    fields       TEXT NOT NULL      -- JSON: the whole record, not a diff
);

-- runs gains:
--   setup_revision_id INTEGER REFERENCES setup_revisions(id)
--   setup             TEXT     -- JSON copy of that revision's fields
```

Whole records rather than diffs, so reading any revision (or a run's copy) is
one row. A diff between two revisions is computed for display.

`effective_at` and `recorded_at` are separate because people write things down
late: a resistor swapped on Tuesday and recorded on Thursday is effective on
Tuesday. See §9 for what that means for runs made in between.

**Facets.** A run's setup copy flattens into facets exactly as `run.metadata`
does now, under `setup.` (`setup.bias_resistor`, `setup.laser.model`), with
quantities (`{value, unit}`) as one leaf. So filtering by apparatus needs no new
machinery on the Data page. `setup` (the name) and `setup.revision` are facets
too.

**Where the project says it.** The project YAML's `run:` block gains
`setup: cryostat-A` and loses `metadata:`. `RunStarted` gains the resolved
revision. A run started from a terminal resolves it the same way, since it
records into the same database.

## 5. The pages

**Setup page** (new top-level section, beside Instruments; see §9 for the name):

- A list of setups; each opens to its **current record** as a form (from the
  template if there is one, free-form fields otherwise, as the metadata editor
  does now) and its **timeline**: revisions newest first, each with its date,
  author, note and what it changed ("bias_resistor 100 kΩ → 50 kΩ").
- Editing and saving adds a revision; the note is asked for then.
- The timeline marks the runs made on each revision, linking to the Data page.

**Run page:** the metadata editor goes. In its place, a setup picker (a
combobox of setups) and a read-only summary of the revision the next run will
record, with a warning when it changed since this project's last run
("bias_resistor changed on Tuesday").

**Data page:** `setup.*` filters in the sidebar; in a run's details, the
setup revision it ran on; and for two selected runs, **what changed** between
their setups (and their instruments' snapshots, which already exist).

## 6. Phases

1. **Record** — the tables, `RunStarted`/recorder support, facets, and
   `setup:` in the project YAML; `run.metadata` removed. Schema version 2.
2. **Setup page** — list, edit (adds a revision), timeline with diffs.
3. **Run page** — setup picker and "changed since last run".
4. **Data page** — what changed between two runs; runs per revision.
5. **Template** — lab-wide fields with types, units and allowed values, if §9
   decides for it.

Each phase is usable on its own; 1 and 2 are the minimum worth shipping.

## 7. What it is not

- Not instrument configuration: addresses and baselines stay in
  `config/instruments`, and a run's instrument settings are still snapshotted
  automatically. The Setup page may *show* the instrument tree beside the
  setup, as one view of "the experiment", but they stay separate records.
- Not measurement params: the sweep, the gate time, stay in the project.
- Not an inventory system: it records what is installed, not what is on the
  shelf.

## 8. Why the lab database, not YAML files

Runs link to revisions, and facets and the "what changed" views need them in
the same place as the runs. Revisions are append-only records, which is what
a database is good at and a hand-edited YAML file is bad at (an edit that
overwrites history is one save away). Export to YAML for reading stays easy,
as it is for runs.

## 9. Open questions

1. **(open) The name.** "Configuration" collides with `config/`, which is
   instrument configuration. Candidates: *Setup*, *Apparatus*, *Bench*,
   *Experiment*. This document says *setup*.
2. **(open) Is the device part of the setup?** The device changes most often
   and is what a run is *about*, so it probably stays its own run field with
   its own registry. But "mounted device" as a setup field would make device
   swaps show on the setup timeline. Suggestion: keep `run.device`, and show
   device changes on the setup timeline by reading runs.
3. **(open) One setup per run, or several composed?** A cryostat record and an
   optics record might change independently and be shared across benches.
   Composition (`setups: [cryostat-A, optics-1550]`) is more flexible and
   harder to explain. Suggestion: one per run to start.
4. **(open) Late entries.** A revision recorded Thursday but effective Tuesday
   means runs on Wednesday recorded the old revision. They keep their copy (the
   rule in §3), but the Data page should say "the setup was corrected after
   this run: bias_resistor was 50 kΩ from Tuesday". Or should re-linking such
   runs be allowed, as an explicit, logged action?
5. **(open) A template, or free-form only?** Free-form is quick to start and
   typo-prone; a template is the fix, but someone has to maintain it.
6. **(open) Per-run extras.** With `metadata` gone, is anything per-run still
   needed besides `notes`? (A per-run temperature reading belongs in the data,
   recorded by a step, not in the setup.)
