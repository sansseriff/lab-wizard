# Procedure composition plan

> **Status: in progress.** Phases 0 and 7, and 6.1-6.3, are built. Phases 1-2
> are useful even if the GUI composer never ships.

How a measurement's *choreography* stops being hand-written Python buried in
`lib/measurements/`, and becomes something composable, storable, and eventually
authorable from the wizard — without inventing a second execution model.

The motivating problem: extending an instrument's role in richer procedures
currently means **adding fields to the instrument's params model**. That is how
the old system ended up with `config['counterInst']['triggerLevelStart']` — a
sweep endpoint filed under a counter. `triggerLevelEnd` and `triggerLevelStep`
follow, and now a procedure lives inside an instrument's config block.

---

## 1. Current state

### Where a parameter can live today

| Home | Example | Frozen per project? |
|---|---|---|
| `config/instruments/*.yml` | `ip_address`, `impedance_ohm` | copied into the project YAML for local instruments; **not** for routed ones |
| project YAML `measurement.params` | `bias.sweep`, `readout.gate_time_s` | yes, always |
| the measurement's Python | the `Step` tree itself | yes — it is copied into the project dir |

### Known defects this plan addresses

1. **The categories are not named, so fields land arbitrarily.** `gate_time_s`
   exists in *both* [`Keysight53220AChannelParams`](../lab_wizard/lib/instruments/keysight53220A.py#L127)
   and [`PCRReadoutParams`](../lab_wizard/lib/measurements/pcr_curve/pcr_curve_params.py#L32).
   The measurement wins at runtime — `CountAtBias` passes it to
   `counter.count(gate_time)` — so the instrument-config value is dead weight
   for this measurement and live for another.
2. **Orphans from an unfinished migration.** `Sim928Params.settling_time` is
   assigned to `self.settling_time` in `__init__` and **never read**; the
   measurement's `settle_s` drives a `Wait` step instead. Same story on
   `Sim921Params` and `Fake928Params`.
3. **Procedures are not first-class.** A procedure is a `build_*_procedure()`
   function inside a measurement module. It cannot be listed, reused across
   measurements, parameterised from config, or composed.
4. **No lab-level defaults layer for procedure params.** Instruments have
   `config/instruments` → project YAML. Measurements have *code defaults* →
   project YAML, with nothing in between. There is no way to say "the lab's
   standard PCR sweep" short of editing every generated project by hand.
5. **`config/measurements/` exists and is empty.** Created 2026-07-14,
   referenced by no code. The slot was carved out and never filled.

### What is *not* broken

The `Step` framework itself. `lab_procedure` is already a behavior tree —
`Status.SUCCESS/FAILED/ABORTED`, `add_child`, `execute` returning a status,
abort propagating to children ([core.py](../procedure_framework/lab_procedure/core.py)).
That is the correct formalism for composable control flow, and nothing below
requires changing it.

Nor is the codegen approach in doubt: [`_compose_setup`](../lab_wizard/wizard/backend/project_generation.py#L283)
already generates instrument wiring with **zero per-instrument branches**. Every
phase below copies that structure.

---

## 2. Core concepts

### 2.1 Three parameter categories

| # | Category | Example | Home | Changes when… |
|---|---|---|---|---|
| 1 | Connection / identity | `ip_address`, `port`, `slot`, `baudrate` | `config/instruments` | the device is replaced or readdressed |
| 2 | Bench wiring facts | `impedance_ohm`, `probe_factor`, `input_range_V` | `config/instruments` | someone rewires the bench |
| 3 | Procedure choreography | sweep bounds, gate time, settle time, thresholds | measurement params | someone designs a different experiment |

**The test.** *If someone rewires the bench, does the value change?* → category 2.
*If someone designs a different experiment on the same bench, does it change?* →
category 3.

In the old script, `impedance` and `coupling` were category 2 (correctly
instrument-ish) and `triggerLevelStart` was category 3 misfiled. That single
mistake is what this taxonomy exists to prevent.

### 2.2 Category 2 is a baseline, not a lock

Category-2 values are applied at connect/arm and drivers already expose setters
for them (`set_coupling`, `set_impedance`, `set_input_range`). A procedure *may*
deviate — but the deviation must be **scoped and self-restoring**, visible in the
Step tree rather than hidden in a config override.

The shape already exists: `params` is the configured baseline, `settings` is the
live working copy, and [`restore_configured_settings()`](../lab_wizard/lib/instruments/keysight53220A.py#L371)
is the defined way home. Generalise it as a `WithSettings(instrument, **overrides)`
step, mirroring `SourceGuard`'s `on_enter`/`on_exit` discipline.

**Discipline rule:** if a measurement overrides a category-2 value *every* time,
it is misfiled. Promote it to category 3 for that measurement, or fix the bench
config. What must **not** happen is the project YAML shadowing category-2 values
— that recreates the drift problem and makes the project a second source of
truth for facts about hardware.

### 2.3 A procedure's signature is its role set

A procedure declares **roles**, each typed by a behavior ABC:

- `iv_curve` → `{voltage_source: VSource, voltage_sense: VSense}`
- `pcr_curve` → `{voltage_source: VSource, counter: Counter}`

That mapping *is* the procedure's type, and [behavior.py](../lab_wizard/lib/instruments/general/behavior.py)
already states the intent: *"an IV curve needs a voltage source, not a Sim928."*

This resolves the naming question directly. A sweep+read and a sweep+count are
**different signatures, therefore different procedures** — but they share a
skeleton and differ only in the leaf step and the role it binds. That is
composition, not two hardcoded measurements.

Most of the machinery exists: `_extract_resources_from_template` already derives
`{variable_name → behavior ABC}` from a setup dataclass's annotations, and
`discover_matching_instruments` already matches configured instruments to a
`base_type` via catalog behavior metadata.

### 2.4 Generate Python; never interpret YAML

A composed procedure is rendered to a `build_<name>_procedure()` function in the
generated project — the same discipline `project_generation` already follows, and
for the same reason `_measurement_source_text` copies the measurement source into
the project directory: *the generated project is the editable unit users run.*

Three consequences worth stating:

- **Editing `config/procedures/<name>.yml` later cannot disturb existing
  projects**, because their step tree is source code in their own directory, not
  a reference. Same freeze principle as instruments.
- **The GUI's expressive limits are not the framework's limits.** When a
  procedure outgrows the composer, you edit the generated Python and stop using
  the composer for that project. No cliff, no second runtime.
- **Round-tripping works** via `_replace_wizard_block`: the GUI owns a marked
  region, hand-edits outside it survive.

### 2.5 Dynamism in three tiers

The instinct to restrict the composer to "non-dynamic" procedures is
unnecessary. Branching costs nothing here.

| Tier | Mechanism | Example | Codegen impact |
|---|---|---|---|
| 1 | Polymorphic params | an `AdaptiveSweepParams` variant beside `LinearSweepParams` | **none** — generated code is identical |
| 2 | Control-flow nodes | `Retry(max_attempts=3, child=…)`, `Guard(condition, body)`, `Selector(…)` | none — they are vocabulary, not special cases |
| 3 | Hand-written `Step` subclass | a closed-loop tuning algorithm | opaque node in the GUI, real Python in the project |

Tier 1 is free because [`SweepParams`](../lab_wizard/lib/measurements/general/sweep_params.py#L69)
is already a discriminated union whose members expose `values()`. Its honest
limit: `values()` is computed before the sweep runs, so it cannot react to
measured data. That is what tier 2 is for.

Tier 2 works because conditions in a behavior tree are just leaf steps that
return `FAILED` instead of `SUCCESS`. No expression language, no interpreter.

### 2.6 Observations are flat; plotting is column selection

There is no dimensionality problem to solve, because a procedure never produces
a "2-D data object". A nested sweep produces **more rows**, each tagged with
every active sweep parameter — `RunContext.bound_parameter` already does this,
and `CountAtBias` already attaches `context.snapshot_parameters()` to every
`Observation`.

So a procedure sweeping attenuation x bias emits N x M flat observations, each
carrying `attenuation_dB`, `bias_voltage`, and `count_rate` — while plain
`mcr_curve`, which sweeps attenuation alone, emits N of them. Same table either
way; the nested case just has more rows. This is the same
insight [database_plan.md](database_plan.md) is built on: *execution structure
and storage structure are decoupled; one row is one integration.*

Which makes plotting a **column-selection** problem, not a reduction problem:

| Plot | x | y | series |
|---|---|---|---|
| IV curve | `bias_voltage` | `sensed_voltage` | — |
| PCR curve | `bias_voltage` | `count_rate` | — |
| MCR curve | `attenuation_dB` | `count_rate` | — |
| MCR at several biases | `attenuation_dB` | `count_rate` | `bias_voltage` |

Pick two or three column names. No dimensionality reduction, no fancy argument
about what is plottable, and the wizard never has to ask for one.

The single exception is genuine per-observation sub-structure — histograms,
time-windowed counts — which the database schema already separates as
`measurement_details` keyed by `detail_type`. That is a *different plot shape*
(a histogram, not a line), not a reduction of the flat table. Two shapes total.

**What the procedure declares** (Phase 4.5) is therefore just the emitted field
names. The benefit is a dropdown instead of a free-text axis field when
configuring a plotter — a UI affordance, not a type system. Savers stay
duck-typed and write whatever arrives.

---

## Phase 0 — Finish the category-3 migration ✅ done

Cleanup with no design risk. Do first; it is independently valuable and it makes
every later phase smaller.

| # | Item | Where |
|---|---|---|
| 0.1 | Delete `settling_time` from `Sim928Params`, `Sim921Params`, `Fake928Params` — assigned to `self`, never read. **Keep it on `Sim970Params`/`Fake970Params`**, where `get_voltage` genuinely sleeps on it ([sim970.py:72](../lab_wizard/lib/instruments/sim900/modules/sim970.py#L72)): an ADC's own read discipline is category 2 | `lib/instruments/sim900/modules/`, `fake_rack/modules/` |
| 0.2 | ~~Decide `Keysight53220AChannelParams.gate_time_s`~~ **Kept, documented.** Not dead: the `Counter` contract says a bare `count()` uses the current gate time, and this seeds it. Procedures pass their own gate time every call and never rely on it | `lib/instruments/keysight53220A.py` |
| 0.3 | Audit every params model for category-3 fields — **done; see the audit below.** Nothing further to delete | all of `lib/instruments/` |
| 0.4 | Add `SetThreshold` beside `SetVoltage` — the missing generic step, and the proof that richer procedures need no new instrument params | `lib/task_adapters/instrument_steps.py` |
| 0.5 | `WithSettings(instrument, overrides, body)` — scoped and self-restoring (see 2.2). Takes a **mapping**, not `**overrides`, so a setting named `name` cannot collide with the step's own name. Restores read-back values, only for overrides actually applied, and never masks the body's own error | `lib/task_adapters/instrument_steps.py` |
| 0.6 | Test flagging start/stop/step-shaped fields (any two of the three, snake or camel case, units suffix allowed) on every instrument params model and its nested models — `tests/test_params_categories.py`, with an `ALLOWED` table that is empty | `tests/` |
| 0.7 | Document the three-category test where instruments are authored — **docs only**, in `docs/concepts/instrument-model.md` (see correction below) | `docs/` |
| 0.8 | ✅ **`pcr_curve` never set the threshold** — it counts with whatever `threshold_mV` the channel's settings hold at arm time. Locally that is the config value; on a server-held counter it is whatever the last client left. Move it into `PCRReadoutParams` and set it explicitly (via 0.4's `SetThreshold`) | `lib/measurements/pcr_curve/` |

**Correction to 0.7.** This plan said the custom-resource flow "walks someone
through authoring an instrument". It does not — it picks already-configured
instruments from the tree and generates a file exposing them. Instruments are
authored as Python `Params` classes, so there is no GUI moment to ask *bench, or
experiment?* The mechanical check in 0.6 is what stands in for that question.

**0.8 has a regression test that fails without the fix:** a counter left at
twice the pulse height by an earlier caller, which on its own counts nothing,
still yields the model's curve at the project's threshold.

### 0.3 audit — every instrument params model

38 models, 199 fields. Connection/identity fields (`ip_address`, `ip_port`,
`port`, `baudrate`, `timeout`, `gpib_address`, `slot`, DBay `mode` /
`direct_port` / `direct_transport` / `serial_port`, module `name`) are omitted.

| Field | Category | Verdict |
|---|---|---|
| `Keysight53220AChannelParams.coupling`, `impedance_ohm`, `input_range_V`, `probe_factor`, `noise_rejection`, `lowpass_filter`, `slope` | 2 | stay — facts about the signal chain on that input |
| `YokoAttenuatorParams` / `Attenuator31Params` / `PowerMeterParams.wavelength_nm` | 2 | stay — which laser is plugged in (and still never pushed to hardware: Phase 7.1) |
| `Attenuator31Params.min_attenuation` / `max_attenuation`, `YokoAttenuatorParams.max_attenuation` | 2 | stay — hardware range; `max_attenuation` is the safe-state target |
| `Sim970ChannelParams` / `Fake970ChannelParams.settling_time`, `max_retries` | 2 | stay — the ADC's own read discipline, used inside `get_voltage` |
| `Fake970Params.device_channel`, `Fake900Params` / `FakeCounterParams.detector_name` and `device`, `SnspdModelParams.*` | 2 | stay — the simulated bench: what is wired to what, and what the device is |
| `visa_timeout_s`, `measurement_timeout_s` | 1 | stay — transport robustness, sized up automatically for long gates |
| `Keysight53220AChannelParams.gate_time_s` | 3 | stay as the bare-`count()` default (0.2); procedures pass their own |
| `Keysight53220AChannelParams.threshold_mode` / `threshold_mV` / `threshold_percent` | **borderline** | stay as the baseline; a procedure that counts sets its own (0.8) |
| `Keysight53220AParams.trigger_count`, `sample_count`, `trigger_delay_s` | **borderline** | an acquisition pattern is a procedure choice, but the counter needs a baseline to arm with. Stay; procedures using multi-reading cycles set them via `configure_trigger` |
| `Keysight53220AParams.trigger_source` / `trigger_slope`, `gate_source` / `gate_polarity` | **borderline** | whether a trigger or gate cable exists is bench; whether *this* run uses it is procedure. Same rule: stay as baseline, set explicitly when used |
| every `offline`, `DBayParams.retain_changes` | none | run-mode switches, not bench or experiment facts — see Open questions |

**The rule for every borderline field is the same:** it stays on the instrument as
the baseline that Phase 7.1 re-applies at run start, and any procedure whose
result depends on it sets it explicitly. Nothing needed promoting or deleting
beyond 0.1.

*Oddity noticed, out of scope:* `Sim921Params.num_channels` is an instance field
where every other model declares `num_channels` as a `ClassVar`.

---

## Phase 1 — `StepSpec`: params models for steps ⬜

The enabling infrastructure. Worth building on its own merits (validation,
docs, intellisense) even if no GUI follows.

Steps currently take plain positional arguments — `SetVoltage(source, voltage)`,
`CountAtBias(counter, bias, gate_time)`. Giving each a params model is the real
cost of everything downstream, and it mirrors instruments exactly:

```python
class StepSpec(BaseModel):
    type: str                       # discriminator, like instruments
    name: str | None = None

    @classmethod
    def step_class(cls) -> type[Step]: ...          # mirrors resource_class()
    def child_slots(self) -> dict[str, StepSpec | list[StepSpec]]: ...
    def render(self, kids: dict[str, str]) -> str: ...   # default covers ~95%

class RoleRef(BaseModel):
    role: str        # "voltage_source" — a name in the procedure's signature
```

- **1.1** `StepSpec` base + `RoleRef`, with a default `render` emitting
  `StepCls(<children>, **scalar_fields, name=…)`.
- **1.2** Specs for the existing vocabulary: `Sequence`, `Sweep`, `Repeat`,
  `Wait`, `SourceGuard`, `SetVoltage`, `TurnOn`, `ReturnToZeroAndOff`.
- **1.3** Control-flow additions: `Retry`, `Guard`, `Selector`, plus condition
  leaves. These are what remove the no-branching restriction.
- **1.4** A `StepSpec` registry mirroring [`resource_catalog`](../lab_wizard/lib/utilities/resource_catalog.py)
  — AST scan, fingerprint cache, `load_params_class` equivalent.

`Sweep`'s `child_factory: Callable[[object], Step]` renders as
`lambda v: <subtree>` in generated Python. This is an argument *for* codegen: a
YAML interpreter would have to reify that closure.

---

## Phase 2 — Procedure storage ⬜

- **2.1** `config/procedures/<name>.yml` — roles (name → behavior ABC), step
  tree, params model shape. Written with the existing
  `model_to_commented_map` / `to_commented_yaml_value` writers, so comments and
  field descriptions survive exactly as they do for instruments.
- **2.2** `config/measurements/<procedure>/<preset>.yml` — named params presets
  for a procedure. This is the missing lab-level defaults layer (defect 4), and
  it fills the empty directory (defect 5).
- **2.3** Preset selection in the generation flow, defaulting to the code
  defaults so nothing changes for existing measurements.

**Vocabulary:** *procedure* = reusable, instrument-generic definition.
*measurement* = procedure + role bindings + params + savers/plotters = a project.

---

## Phase 3 — Codegen ⬜

- **3.1** Tree walk: render children, then ask each spec to render itself.
  The whole generator is roughly

  ```python
  def render(spec: StepSpec) -> str:
      kids = {slot: render(c) for slot, c in spec.child_slots().items()}
      return spec.render(kids)
  ```

  **No per-procedure cases, ever.** Each `StepSpec` owns its rendering; adding a
  step type requires no generator change. This is the same property
  `_compose_setup` already has for instruments.
- **3.2** `RoleRef` → setup-dataclass field resolution. The one piece of wiring
  the generator knows about, and it is uniform.
- **3.3** Emit `build_<name>_procedure()` into the project directory, inside
  `# wizard:` markers so hand-edits outside survive.
- **3.4** Hand-authored measurements keep working: `get_measurements` discovers
  from the directory today, and composed procedures become a *second source*
  presenting the same shape (roles + params model + something renderable).
  Nothing should ever be forced through the GUI.

---

## Phase 4 — The Procedures section ⬜

A seventh top-level section in [`Sidebar.svelte`](../lab_wizard/wizard/frontend/src/lib/components/Sidebar.svelte),
beside Overview, Measurements, Instruments, Servers, Plotters, and Data. Its
docstring says "Six top-level sections" and will need updating.

Requisite abilities, minimal set:

| # | Ability | Notes |
|---|---|---|
| 4.1 | Declare roles | name + behavior ABC from the registered behaviors; this is the signature |
| 4.2 | Compose the tree | nest from a palette driven by the 1.4 registry |
| 4.3 | Leaf operations filtered by role | pick a role → see the operations its ABC offers. The ABCs already carry the right method sets, with docstrings and units |
| 4.4 | Narrow a role to a concrete type | `VSource` → `Sim928` widens the operation list and narrows what can bind. Make the tradeoff **visible** — it is exactly the portability cost |
| 4.5 | Declare emitted data | the field names each leaf puts in its `Observation`. Feeds the plotter's axis dropdowns — see 2.6 |
| 4.6 | Mark params vs literals | which scalars are exposed in the params model |
| 4.7 | Save | → `config/procedures/<name>.yml` |

4.3 is what makes the window feel good to use, and it is cheap.

---

## Phase 5 — Measurement creation, rewired ⬜

Largely additive — the existing two-page flow survives.

- **5.1** `/measurements/new` lists procedures (composed and hand-authored
  alike) instead of directory-discovered measurements only.
- **5.2** `/measurements/resources` binds roles to concrete instruments —
  already what it does, driven by `discover_matching_instruments`.
- **5.3** Params preset selection (Phase 2.3).
- **5.4** **One source rule, no per-project switch.** Instrument params
  (categories 1 and 2) always come from a `config/instruments` tree — this
  workspace's for a local instrument, the server workspace's for a routed one.
  Procedure params (category 3) always come from the project YAML. The project
  YAML stops carrying instrument params at all.

  This makes local and routed **one pattern**: a project names instruments by
  `attribute_name` and resolves them against a tree it does not own. The only
  difference is *which* tree answers, which `instrument_sources` already records.

  Because categories 1 and 2 share a home, **no params model is ever split at
  load time** — the instrument's params object is loaded whole, and category 3
  reaches hardware as method arguments. The three-way taxonomy is an authoring
  discipline; the runtime boundary is two-way.

- **5.5** **Reference by `attribute_name`, never by hash.** Today's production
  style emits `Cls.from_config(resources, key='c0ca3509')`, and that hash is
  `instrument_hash(type, <address>)` — derived from category 1. Readdressing an
  instrument centrally would break every local project that names it. So
  generation moves to `from_attribute` for local instruments too, and
  `attribute_name` becomes a stable public identifier: renaming or removing one
  must list the projects that reference it first, the same pattern 8.5 of
  `server_plan.md` uses for permission rules.

- **5.6** **Every run starts from the configured baseline — guaranteed, not
  assumed.** The intended model: category 2 is re-applied from config at the
  start of each run, so a non-default left behind by a previous experiment can
  never leak into the next one. Today that holds only by accident:

  | Situation | Starts from baseline? | Why |
  |---|---|---|
  | Local run, driver applies settings at arm (Keysight) | yes | fresh process, `settings = params.model_copy()`, arm writes it |
  | Local run, driver never pushes the param | **no** | Yokogawa and Ando `wavelength_nm` are stored in params and never written — the hardware keeps whatever was last set |
  | Routed run, any driver | **no** | the server's instrument is a singleton that lives until release; a previous client's `set_coupling("AC")` stays in its `settings` and is re-applied at the next arm |

  `restore_configured_settings()` exists on the Keysight channel and has **zero
  callers**. So the fix is a run-start reset: an `apply_baseline()` on the
  instrument contract that every driver with category-2 params implements, and
  that the generated procedure calls on each bound instrument before anything
  else. With that in place, the leaked-state concern disappears.

  What remains is smaller and optional: the baseline itself can be *edited*
  between runs, so recording the applied category-2 values into
  `RunStarted.config` answers "which default was this taken at" months later.
  Locally that is a read of the config tree; through a server it needs a read
  RPC, since nothing exposes params today. Nice-to-have provenance, not a
  correctness issue — defer it.

- **5.7** **Everything that reads the project's instrument copy** must move to
  the config tree:

  | Reader | Where |
  |---|---|
  | `Child.from_config` | [parent_child.py:463](../lab_wizard/lib/instruments/general/parent_child.py#L463) |
  | `Keysight53220A.from_config` override | [keysight53220A.py:654](../lab_wizard/lib/instruments/keysight53220A.py#L654) |
  | `preflight_local_project` — takes transport keys from the project copy | both setup templates, line 96 |
  | hash repair on project load | [model_tree.py:246](../lab_wizard/lib/utilities/model_tree.py#L246) |
  | subset copy in custom-resource generation | [custom_resource_generation.py:705](../lab_wizard/wizard/backend/custom_resource_generation.py#L705) |
  | Projects page "Bound to" column | `routes/measurements/projects/+page.svelte` |
  | eager `InstrumentRegistry(resources)` / server `project_yaml` mode | hosts a project's instrument copy, which no longer exists — retire it |

- **5.8** **Projects are no longer self-contained.** A local project needs its
  workspace's `config/` at run time. `find_workspace_config_dir` already exists
  and `load_server_urls` uses it; a project run outside a workspace needs a
  clear error naming what it looked for.

- **5.9** **Legacy projects keep working.** Existing projects (e.g.
  `projects/pcr_curve_20260806_143308`) carry a full instrument tree and use
  hash lookup. Keep that read path; stop generating it. Do not migrate silently.

- **5.10** **Teaching styles.** Retire `pedagogical_yaml_expanded` — verbose
  wiring that still reads params from elsewhere teaches the hash traversal this
  plan removes and serves no case the other styles do not. **Keep
  `pedagogical_embedded`** as the deliberate exception to 5.4: everything in one
  Python file, for messy testing and for ripping a project out of the
  lab_wizard ecosystem entirely. It breaks when an instrument is readdressed,
  and that is accepted. Label it as the escape hatch, never the default.

---|---|---|
  | 1 — connection / identity | `config/instruments` | only when the instrument is local |
  | 2 — bench wiring | `config/instruments` | yes |
  | 3 — procedure | project YAML | yes |

  **Category 1 cannot be project-local for a routed instrument** — connection
  params define *which device*, so a project holding its own would be naming a
  different instrument, not overriding one. Enforce this in the picker rather
  than discovering it at run time.

  Record the choice in the project YAML beside `instrument_sources`, for the
  reason [composite_resources](../lab_wizard/lib/client/composite_resources.py)
  already gives: a project should run identically for everyone and stay readable
  six months later, which a remembered generation-time flag is not.

---

## Phase 6 — `Attenuator` ABC and `mcr_curve` — 6.1-6.3 ✅ done, 6.4-6.6 ⬜

The first procedure *authored* rather than hand-written, and the honest test of
whether Phases 1-5 work. `mcr_curve` sweeps optical attenuation and records
count rate, so its signature is `{attenuator: Attenuator, counter: Counter}` —
the first whose swept role is not a `VSource`.

### The ABC has two implementers already, and they disagree

Both are current-pattern `Child` classes, and neither can be bound by any
measurement because there is no ABC for them to satisfy:

| Concept | [Yokogawa AQ2212](../lab_wizard/lib/instruments/yokogawaAQ2212/modules/attenuator.py) | [Ando AQ8201-31](../lab_wizard/lib/instruments/andoAQ8201A/modules/attenuator31.py) |
|---|---|---|
| set attenuation | `set_attenuation(atten_db)` | `set_attenuation_db(attenuation_db)` |
| get attenuation | `get_attenuation()` | only `get_status()` → `(wavelength, atten)` |
| set wavelength | `set_wavelength_nm(wav_nm)` | `set_wavelength_nm(wavelength_nm)` |
| block the light | `set_output(False)` | `close_shutter()` |
| read that state | `get_output_status()` | — none — |

Five ways to spell four ideas. This is exactly the divergence a behavior ABC
exists to collapse, and the Yokogawa attenuator is **already configured and
served** — `config/instruments/yokogawa_aq2212_key_b3c9ab43/`, attribute
`yoko_attenuator-broad-chicken` at `inst://b3c9ab43/54d59b04`.

- **6.1** Add the `Attenuator` behavior ABC (TERMINAL). Follow `Counter`'s
  reasoning: only what a measurement cannot work without, and **keep the
  getter** — attenuators quantize and clamp, and an MCR curve is only
  interpretable against the value actually applied. Ando needs a real
  `get_attenuation` extracted from its `get_status` tuple.

  Applying the 2.1 test is instructive: `set_attenuation` / `get_attenuation`
  are category 3 (the procedure sweeps them, so they are arguments);
  `wavelength_nm` is category **2** — a bench fact about which laser is plugged
  in — and it is *already* in both params models, which is the taxonomy
  independently confirming itself.

  Per "Decisions taken", the ABC also declares its safe state: **maximum
  attenuation, shutter closed.**
- **6.2** Resolve the name collision. The Yokogawa concrete class is literally
  named `Attenuator`. Repo convention is that the ABC takes the generic name and
  concrete classes are specific (`Counter` / `Keysight53220AChannel`,
  `VSource` / `Sim928`), so rename to `YokoAttenuator` / `YokoAttenuatorParams`.
  Safe for the config tree: `instrument_hash(type_str, key_value)` hashes the
  `type` literal and the addressing value, never the class name, and
  `type: "yoko_attenuator"` is unchanged — so **no YAML moves and no hashes
  change.**
- **6.3** Conform both implementers to the ABC, and register an `Attenuator`
  proxy in [`proxies/registry.py`](../lab_wizard/lib/client/proxies/registry.py)
  so the behavior works through a server. The auto-forwarder makes that a
  one-line class.

  **As built (6.1-6.3):** `lib/instruments/general/attenuator.py` with
  `set_attenuation` / `get_attenuation`, `open_shutter` / `close_shutter`,
  `get_max_attenuation`, a concrete `enter_safe_state`, `_state_methods_` for
  attenuation and shutter, and `StandInAttenuator`. Decisions made while
  building:
  - **Shutter verbs, not "output".** The AQ2212 calls its shutter the module's
    output; `open_shutter` / `close_shutter` say what happens to the light and
    make the safe state unambiguous. Reading the shutter back is not on the ABC,
    because the AQ8201-31 cannot; `YokoAttenuator.is_shutter_open()` keeps it.
  - **`get_max_attenuation` reads a param** (`max_attenuation`, dB), added to
    `YokoAttenuatorParams` to match Ando's existing field name. Querying the
    hardware would be better, but the AQ2212's SCPI for it is unverified.
  - **Offline getters return the last commanded value.** Both slot deps answer
    every offline query with `""`, which the getters would otherwise fail to
    parse.
  - The configured `yoko_attenuator-broad-chicken` now reports
    `behavior_abc: Attenuator` through the server registry, and an `Attenuator`
    requirement matches both drivers in the picker. 19 tests in
    `tests/test_attenuator.py`.
  - The Ando status-parser test is shaped to the existing parser
    (wavelength at characters 6-10), not to a captured instrument reply.
- **6.4** Build `mcr_curve`. It does not exist — only a commented-out import in
  [`lib/__init__.py`](../lab_wizard/lib/__init__.py#L46) and an `MCR_CURVE` enum
  value in [`schema.py`](../lab_wizard/lib/savers/schema.py#L27).
  (`docs/roadmap.md` says it still uses a jinja template; that is stale — there
  is nothing to migrate.) Compose it in the Procedures window and generate a
  project. **If this requires touching the generator, Phase 3 failed its
  no-per-procedure-cases property** and should be fixed rather than
  special-cased.
- **6.5** *(later)* A `Laser` ABC has an implementer waiting too —
  [`yokogawaAQ2212/modules/laser.py`](../lab_wizard/lib/instruments/yokogawaAQ2212/modules/laser.py),
  with the same `set_output` / safe-state shape. Not needed for MCR; noted so
  6.1's ABC design stays honest about being the second of three, not a one-off.
- **6.6** *(optional)* Port [`AgilentN7764A`](../lab_wizard/lib/instruments/agilentN7764A.py)
  — 4-channel, legacy `AgilentN7764AConfig(BaseModel)` with no `type: Literal`,
  no `resource_class()`, no `create_inst()`, so `resource_catalog` cannot see it
  at all. It would become a `ChannelProvider[AgilentN7764AChannel]` with
  `Attenuator` on the channel, structurally identical to `Keysight53220A`.
  Deferred: two working implementers are enough to define the ABC against.

---

## Phase 7 — Run lifecycle ✅ done

Phases 0-6 decide what a procedure *is*. This decides what happens around one
when it runs, and it is where the decisions in 5.4-5.6 and server Phase 9 meet.
Every generated run does exactly this, in this order:

```
1. claim       local instruments: transport lease per exclusive root, then preflight
2. resolve     construct the instruments
3. claim       routed instruments: claim_acquire, all-or-nothing (server Phase 9)
4. baseline    apply_baseline() on every bound instrument
5. run         the measurement
6. safe        enter_safe_state() on every bound instrument that declares one — if the run did not succeed
7. release     claims and leases, always
```

The order is not arbitrary. **Claim before resolve**, because constructing a
local rack can open its serial port, and opening before claiming is the race
claims exist to close. **Claim before baseline**, or another client can change a
setting between our reset and our first step. Baseline is applied to what the
run claimed — a channel holder resets its channel, not the shared counter state
it has no right to write, and relies instead on server 9.12 having left
unclaimed ancestors at baseline. **Safe before release**, or a failed run hands
the next holder a biased source.

### Corrections made while building

- **Resolve comes after claim, not before.** The plan listed resolve first. For
  routed instruments either order works; for local ones it does not.
- **Safe state runs only when a run does not succeed.** The plan ran it after
  every run. But `iv_curve` exposes `turn_off_at_end: false` as a deliberate
  choice, and a completed run has already been through its own
  `SafeGuard`/`SourceGuard`. So the lifecycle is the *backstop* — failure,
  abort, exception, Ctrl-C — and a successful run is left where its procedure
  ended it. After an exception a failed safe state is logged, never raised, so
  the original error survives; after a failed or aborted *status* it is raised,
  because a source left biased must not pass as a quiet non-zero exit.
- **Preflight is still needed; acquisition does not subsume it.** A lease stops
  other processes, and stops a server opening the rack later, but cannot see a
  server that opened it *before* the lease. So the claim leases first and then
  preflights: once leased no server can open the rack, and preflight catches any
  that already had. Either refusal releases every lease taken.
- **`apply_baseline` is concrete, not abstract.** It lives on
  `InstrumentBehavior` as a successful no-op — right for any instrument whose
  params are all connection and identity — and drivers with bench-wiring params
  override it. A concrete ABC method would run client-side on a proxy and reset
  nothing, so `RemoteProxy` forwards it explicitly (`_ALWAYS_FORWARDED`). Unlike
  `enter_safe_state`, it touches no safety state, so one opaque call is fine.

### Items

- **7.1** ✅ **`apply_baseline()`**. Default no-op on `InstrumentBehavior`.
  Overridden by `Keysight53220AChannel` (this input's conditioning;
  `restore_configured_settings` kept as an alias), `Keysight53220A` (trigger
  written now, gate and timeout re-written at the next arm via
  `_forget_hardware_state`, then every input), `YokoAttenuator` and
  `Attenuator31` (the configured `wavelength_nm`, which was never sent before).
  *Not done:* `PowerMeterParams.wavelength_nm` is still never pushed — the power
  meter has no behavior ABC, so no run binds it and nothing would call it.
- **7.2** ✅ **`enter_safe_state()`** on `VSource` (0 V, then off) and
  `Attenuator` (6.1). Concrete on the ABC, so through a proxy it decomposes into
  calls the permission gate records individually — see the correction in 6.1.
  `Laser` waits for its ABC (6.5).
- **7.3** ✅ **`_query_methods_`**, merged across the MRO by
  `collect_query_methods` as a **union** — a subclass can add a query but not
  quietly turn an inherited one into a write. Declared on `VSense`
  (`get_voltage`, `measure`), `Counter` (`get_gate_time`, `get_threshold` — *not*
  `count`, `count_rate` or `measure`, which arm the counter), `Attenuator`
  (`get_attenuation`, `get_max_attenuation`), and `YokoAttenuator`
  (`is_shutter_open`, `get_wavelength_nm`). `VSource` has no getters to declare.
  Nothing consumes the declarations until server 9.5.
- **7.4** ✅ **`RunLifecycle`** in `lib/task_adapters/lifecycle.py`, with
  `run(resolve, execute)` rather than wrapping `ProcedureRunner.run`: the
  measurement class keeps wiring its savers and plotters, and the lifecycle
  needs `resolve` as a callable to construct instruments *after* claiming.
  `bound_instruments` finds behaviors in the resources' fields and lists,
  once each; a `RemoteOpaque` is left untouched. Claims are any context
  managers, entered all-or-nothing. `LocalTransportClaim`
  (`lib/client/local_claims.py`) is the local one.
- **7.5** ✅ **`SafeGuard(instrument, body)`**, refusing at construction an
  instrument whose class declares no safe state (checked on the class, since a
  proxy answers any attribute). `SourceGuard` is a **subclass rather than an
  alias**: it keeps its three flags, because `IVSafetyParams` exposes them; with
  both exit flags set its exit *is* the declared safe state, and a partial exit
  is honoured. Exit failures raise after a successful body and are logged after
  a failed one, the same rule as `WithSettings`.
- **7.6** ✅ **Generated setup files run through `RunLifecycle`**, claim local
  transports in the local and mixed branches (none under `--remote`), and exit
  non-zero when a run does not succeed. Server 9.11 is therefore done too. An
  end-to-end test runs a generated PCR script while the test process holds the
  counter's lease, and the script stops naming the holder.
- **Tests:** `tests/conftest.py` now points `LAB_WIZARD_LEASE_DIR` at a
  temporary directory for every test, including generated scripts run as
  subprocesses. `test_run_lifecycle.py`, `test_local_claims.py`,
  `test_baseline_and_safe_state.py`; 48 new tests. Mutating the channel
  baseline to a no-op, or making the lifecycle run safe state after success,
  fails them.

---

## Why this shrinks the server problem

Once category 3 lives in `measurement.params`, it is project-local and frozen
**by construction**, and it reaches hardware through *method calls*
(`counter.count(gate_time)`, `source.set_voltage(v)`) — which `RemoteProxy`
already forwards over the wire.

So a **routed instrument gets the project's frozen procedure params for free**.
No settings-push RPC, no server-side params merge, no new wire method.

The residual drift exposure is category 2, applied by `apply_input_settings()`
at arm time from whichever tree owns the instrument. When routed, that is the
server's — which is *correct*: if someone swapped the coax to 1 MΩ, a project's
frozen "50 Ω" would be a lie.

---

## Build inventory

Everything that has to be created or changed, across this plan and
`server_plan.md` Phase 9, grouped by layer. Phase numbers point at the rationale.

### Instrument layer — `lib/instruments/`

| Item | Kind | Phase |
|---|---|---|
| ✅ `Attenuator` behavior ABC — set/get attenuation, shutter, `get_max_attenuation`, concrete `enter_safe_state` | new | 6.1 |
| ✅ `_query_methods_` declarations on `Attenuator` | new | 7.3 |
| ✅ Rename Yokogawa `Attenuator` → `YokoAttenuator`; conform Yoko + Ando to the ABC | change | 6.2, 6.3 |
| ✅ `enter_safe_state()` on `VSource` — concrete, like `Attenuator`'s | new method | 7.2 |
| ✅ `apply_baseline()` on every behavior driver with category-2 params; push the dead `wavelength_nm` (power meter still pending — no ABC) | new method | 7.1 |
| ✅ `_query_methods_` merge helper in `state_effects.py`; declarations on `VSense`, `Counter`, `Attenuator`, `YokoAttenuator` | new | 7.3, server 9.2 |
| ✅ `children_claimable()` on params — false by default; true for `Keysight53220A`, GPIB buses, SIM900, DBay, AQ2212, AQ8201A | new method | server 9.1 |
| ✅ Delete dead `settling_time` on `Sim928`/`Sim921`/`Fake928`; document `gate_time_s` | cleanup | 0.1, 0.2 |
| ✅ `Attenuator` proxy class | new, one line | 6.3 |

### Procedure layer — `lib/task_adapters/`, `lib/procedures/`

| Item | Kind | Phase |
|---|---|---|
| ✅ `SetThreshold`, `WithSettings` steps | new | 0.4, 0.5 |
| ✅ `SafeGuard`; `SourceGuard` as a subclass keeping its flags | new | 7.5 |
| `StepSpec`, `RoleRef`, spec per existing step | new | 1.1, 1.2 |
| `Retry`, `Guard`, `Selector`; `ValueAbove` / `ValueBelow` / `StepFailed` | new | 1.3 |
| `StepSpec` registry mirroring `resource_catalog` | new | 1.4 |
| ✅ `RunLifecycle`, `LocalTransportClaim` | new | 7.4 |
| ✅ `pcr_curve` sets its threshold from `PCRReadoutParams` | change | 0.8 |
| `mcr_curve` | new, composed | 6.4 |

### Server and client — `lib/server/`, `lib/client/`

| Item | Kind | Phase |
|---|---|---|
| ✅ Threaded request dispatch | change | server 9.0 |
| ✅ `ClaimTable`; `claim_acquire` / `renew` / `release` / `list` / `force_release` RPCs | new | server 9.3, 9.4 |
| ✅ Claim + query check in `WireServer.call`; `ClaimDeniedError` | change | server 9.5 |
| ✅ `RemoteClaim` / `RoutedClaims` (`client/claims.py`); `Session` attaches token | new | server 9.7 |
| ✅ `tree_*` and `release` refuse while claimed; claim events in audit log | change | server 9.8, 9.9 |
| ✅ Hierarchical disjointness check in `claim_acquire`; baseline restore on release/expiry | new | server 9.4, 9.12 |
| ✅ Interleaving test: two runs on channels 1 and 2 of a `FakeCounter` | new test | server 9.13 |

### Config and generation — `lib/utilities/`, `wizard/backend/`

| Item | Kind | Phase |
|---|---|---|
| `config/procedures/` storage; presets in `config/measurements/` | new | 2.1, 2.2 |
| Procedure codegen (tree walk, role resolution, wizard blocks) | new | 3 |
| Generation switches local instruments to `from_attribute`; stops copying params into project YAML | change | 5.4, 5.5 |
| Seven readers of the project's instrument copy move to the config tree | change | 5.7 |
| Attribute-reference scan across `projects/` + rename/remove guard | new | 5.5 |
| Run-time workspace lookup with a clear failure outside a workspace | change | 5.8 |
| Remove `pedagogical_yaml_expanded` (backend, frontend radio, tests) | delete | 5.10 |
| ✅ Generated setup files call `RunLifecycle`; acquire leases for local roots | change | 7.6, server 9.11 |

### Frontend — `wizard/frontend/`

| Item | Kind | Phase |
|---|---|---|
| Procedures section — seventh sidebar entry, composer | new | 4 |
| Procedure + preset selection in measurement creation | change | 5.1, 5.3 |
| ✅ Live claims and force-release on Hardware ownership | new | server 9.10 |
| Show a claimed instrument as busy in the measurement picker | new | server 9.10, deferred to 5 |
| Attribute rename/remove confirmation listing dependent projects | new | 5.5 |

### Deferred

Recording the applied baseline into `RunStarted.config` (5.6), plus the params
read RPC it needs through a server; a generation-time params fingerprint with a
run-start drift warning; porting `AgilentN7764A` (6.6); the `Laser` ABC (6.5).

---

## Decisions taken

- **No procedure versioning.** Projects freeze their tree as generated Python,
  which is the only guarantee that matters. Multiple live revisions of one
  procedure are not a use case; if that changes, the freeze property means it
  can be added later without disturbing anything already generated.
- **Plotting is column selection, not dimensionality reduction** — see 2.6.
- **Condition leaves stay a small fixed set**: `ValueAbove`, `ValueBelow`,
  `StepFailed`. Tier-2 dynamism is a minor use case initially, so no expression
  language, and no attempt to anticipate the general case.
- **Instrument params never live in a project.** Categories 1 and 2 always
  come from a `config/instruments` tree, local or through a server; category 3
  always comes from the project YAML. No per-project switch. GUI help for
  category 1 stays where it is — the add-instruments workflow — and category 1
  never crosses a server connection at run time: only attribute names and
  method calls do. See 5.4-5.10.
- **One escape hatch from the params rule:** the embedded teaching style, which
  puts every param in the Python file. Brittle by design. The YAML-expanded
  style is retired. See 5.10.
- **A procedure sets every category-3 value it depends on.** Never rely on an
  instrument's current setting: on a shared server instrument, "current" is
  whatever the previous client left. A correctness rule, not a style one.
- **Claims are a borrow checker over the instrument tree.** Writes need a
  run-scoped claim, reads need nothing, and claims by different runs must be
  disjoint: a claim covers its subtree, siblings coexist, ancestors and
  descendants exclude each other. Claims are per run, not per role. Channel
  claims are allowed only where a driver declares its channel operations
  transactional; otherwise a claim resolves to the whole instrument.
  **Unclaimed state is baseline state** — release and expiry restore it.
  Designed in `server_plan.md` Phase 9.
- **The 53220A's channels are claimable separately.** Its manual confirms one
  measurement engine: `CONFigure` selects function and channel together, one
  system trigger, gate scoped by function, only `INPut[{1|2}]` per input. It
  cannot count on both inputs at once, but interleaved counts are correct
  because `count()` arms and reads in one call and re-arms from settings on a
  cache miss. Trigger and gate are root state, changeable only under a root
  claim. The driver's box-level trigger/gate and per-input threshold are
  already right.
- **Behavior ABCs declare their own safe state.** There are few of them and each
  has an unambiguous answer: `VSource` is 0 V and output off, `Attenuator` is
  *maximum* attenuation with the shutter closed, a future `Laser` is output off.
  Putting it on the ABC is what lets one guard step work for all of them —
  see the `SourceGuard` question below.

## Open questions

- **How far does `SourceGuard` generalise?** It is `VSource`-specific
  (`turn_on` / `set_voltage(0)` / `turn_off`), but the safe-entry/guaranteed-exit
  pattern is not. MCR wants the same discipline around an attenuator's shutter.
  Either each behavior gets its own guard step, or `SourceGuard` becomes a
  generic wrapper parameterised by enter/exit steps. Decide when the second case
  actually exists — which is Phase 6.
- *Resolved into [`server_plan.md`](server_plan.md) Phase 9:* leaf ownership.
  Writes require a run-scoped claim, reads require nothing, and claims by
  different runs must be disjoint subtrees. Channel-level claims are allowed
  where a driver's operations are transactional, so two runs can interleave on
  channels 1 and 2 of one counter. "Read" is a fail-closed query allowlist
  rather than a reuse of `_state_methods_`.
- **Same device configured in two trees** is a config-hygiene warning, not a
  concurrency problem. Running at the same time is already refused by
  transport leases, and will be by leaf claims. What locks cannot catch is two
  *sequential* runs from two workspaces, each applying its own tree's baseline
  to one physical channel. Warn when adding an instrument whose `transport_key`
  a machine-local server already serves, and steer the picker to routing.
- **`offline` sits outside the taxonomy.** It is a run-mode switch, not a fact
  about the bench or the experiment. With instrument params central, nobody can
  run one project simulated while another runs live against the same config.
  Probably wants to be a run-time flag rather than a params field.
- **Does `SourceGuard` become one generic `Guard`?** With safe state on the ABC
  (above), a single `SafeGuard(instrument, body)` calling
  `instrument.enter_safe_state()` on exit would cover `VSource`, `Attenuator`,
  and `Laser` alike, and `SourceGuard` would become a thin alias. Worth doing
  once the second implementer exists rather than speculatively.
