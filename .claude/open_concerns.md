# Open Concerns / Critical Assessment

**Written:** 2026-05-28
**Basis:** full read of the current source tree (not the plan docs). Each claim
below was verified against code; file references included.

## Headline

The infrastructure is genuinely well-built and well-tested, but the one flow that
is the actual product — **generate a measurement → run it → get data** — is
broken end to end, and it is the *only* flow with no test coverage. The ~19-file
test suite covers config / discovery / remote / permissions / project generation
but never calls `run_measurement()`. That is why the breakage below survives.

Mental model: a very good chassis with the drivetrain not connected to the wheels.

---

## Tier 1 — Makes the library unusable right now

### 1. A generated measurement crashes on the first line of `run_measurement()`
`lab_wizard/lib/measurements/iv_curve/iv_curve.py` was written against an older,
dead instrument interface that no longer exists.

- Calls `voltage_source.connect()`, `.set_output()`, `.enable_output()`,
  `.disconnect()`, and `voltage_sense.measure()`/`.configure_measurement()`.
  The current `VSource` ABC (`lab_wizard/lib/instruments/general/vsource.py`)
  defines only `set_voltage` / `turn_on` / `turn_off`. **None of
  `connect`/`set_output`/`enable_output` exist** → `connect_instruments()`
  `AttributeError`s on entry (iv_curve.py:75).
- Reads `self.params.voltage_sequence()`, `.settling_time`, `.enable_plotting`,
  `.save_data` (iv_curve.py:63,156,181,271). The actual `IVCurveParams` in the
  template defines only `start_V, end_V, step_V, bias_resistance`.
- References `self.voltage_plotter`, `self.current_plotter`, `self.data_handler`
  (iv_curve.py:159,206,272) — never assigned in `__init__`.

The measurement class and the `Resources` contract it is handed are from two
different eras of the codebase.

### 2. The data pipeline is plumbed but never connected
The wizard threads `savers`/`plotters` into the generated `Resources` dataclass,
but **no measurement class calls** `saver.start_run` / `write_measurement` /
`end_run` or `plotter.plot` (grep across all of `lib/measurements/` returns
nothing). `DatabaseSaver` is fully working and tested *in isolation* but has no
caller in the measurement path.

### 3. Plotters are fake
`MplPlotter` (`lib/plotters/mpl_plotter.py`) and `BokehPlotter`
(`lib/plotters/bokeh_plotter.py`) only store the last payload and `print()`. No
rendering. No file saver exists at all. The savers/plotters surface is one real
class (`DatabaseSaver`) plus four stubs.

**Net effect:** the wizard's central promise does not work for any measurement,
with any instrument, today.

---

## Tier 2 — Will cause real problems / silent wrongness

### 4. The project YAML `exp:` block is dead config
`wizard/backend/project_generation.py` `_exp_defaults` writes measurement params
into the YAML (`start_voltage`, `stop_voltage`, …), but the setup template
constructs `params=IVCurveParams()` with **no arguments**, and `Exp.exp`
(`lib/utilities/model_tree.py`) is never parsed by type or read by the
measurement. Editing the sweep range in the generated `.yaml` has **no effect**.
Field names don't even match (`start_voltage` vs `start_V`). Config that looks
authoritative but is inert — a trap.

### 5. `mcr_curve` is a landmine in the measurement list
Its template still uses the old `{{jinja}}` style; importing it evaluates
`{{voltage_source_class}}` → `NameError`, so `/api/get-resources/mcr_curve`
returns an error dict instead of a list and the UI breaks. Only `iv_curve` and
`pcr_curve` use the new `# wizard:*` block format, but all three are offered.

### 6. Discovery is regex-on-source with undocumented hard constraints
`lib/utilities/params_discovery.py` scans source text, not AST/imports:
- Every params class **must be named `*Params`** or it is invisible.
- `type: Literal["x"]` must be one line in that exact shape.
- Metadata building does `except Exception: pass` → a params class that throws on
  `cls()` **silently vanishes from the GUI** with no diagnostic.
- Cache file `~/.cache/lab_wizard/params_cache_instrument.json` is keyed only by
  *kind* → **shared across every checkout/clone on the machine**; invalidated
  only by an mtime+count fingerprint.

### 7. Server is single-threaded
`lib/server/wire.py` is one poll loop — one slow `set_voltage` blocks all clients
globally. This undercuts the stated motivation (one server multiplexing a shared
VISA connection across multiple client programs): it serializes everything, not
just same-connection calls. Also no graceful instrument teardown, no client
reconnect, no permission hot-reload.

### 8. Deprecated `datetime.utcnow()` on a Python ≥3.14 project
`lib/savers/schema.py:72,90` and `lib/savers/database_saver.py:171`. Deprecated
since 3.12; noisy now and on a path to breakage. Also stores naive UTC →
ambiguous timestamps on read.

---

## Tier 3 — Friction / adoption barriers

- **`requires-python = ">=3.14"`** is aggressive (3.14 brand new). Transitive deps
  may lack wheels; can block installation outright.
- **Error-handling inconsistency**: `/api/get-resources` can return a
  `{"error": ...}` dict where the frontend expects a list; discovery/metadata
  errors are swallowed. Failures present as "nothing here" rather than a
  diagnostic.
- **`from __future__ import annotations` trap**: one such line in a setup template
  stringizes the `Resources` annotations and silently makes the measurement
  appear to need no resources (extractor reads `__annotations__` raw). Known and
  documented but a real footgun.
- **Legacy dead code**: `wizard/wizard.py` imports modules that no longer exist.

---

## What is solid (don't rewrite)

Instrument model (Params/Instrument, parent/child/channels, KeyLike, behavior
ABCs), config tree + hashing + I/O, remote server/client/proxy layer, permission
state machine, DB schema. Well-factored, internally consistent, well-tested.

---

## Minimum path to "usable"

1. Rewrite `IVCurveMeasurement` (and `pcr_curve`) against the real
   `Resources`/behavior-ABC contract — use `set_voltage`/`get_voltage`, drop the
   dead `connect`/`set_output` calls, and call the saver lifecycle.
2. Connect the project YAML `exp:` params to the measurement params object (one
   source of truth) — or stop writing them.
3. Add **one end-to-end test**: generate a project with stand-in instruments +
   `DatabaseSaver`, run it, assert rows written. This single test would have
   caught all of Tier 1 and guards it forever.
4. Either fix or hide `mcr_curve`.
5. Ship one real plotter (even a blocking matplotlib one) and a file saver.
