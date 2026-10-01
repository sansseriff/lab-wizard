---
icon: lucide/map
---

# Roadmap / what still needs work

An honest inventory of what is **scaffolded but not finished**, checked against
the current source. Look here before assuming a feature works end to end.

## Plotters

- ✅ **Live plots.** A run started from the wizard is drawn on its Run page. From
  a terminal, `outputs.live_plot: window` opens a matplotlib window and `web`
  the live page (a window here, a link over SSH). All of them read the run from
  the lab database as it records. See [The Run page](wizard/measurements.md#the-run-page).
- ⚠️ **The matplotlib window draws one plot** (the one `outputs.plot` names, or
  the first); the web page and the Run page have a tab for each.
- ❌ **No progress within a step.** A long `Wait` shows as a growing bar, not a
  fraction done.

## Data

- ✅ **Every run is recorded** in the workspace's `data/lab.db`: one row per
  point, every step, the device and run metadata, the instruments' settings, and
  the procedure it ran, with its plots and derived columns. See
  [The lab database](data/database.md).
- ✅ **The [Data page](wizard/data.md)** filters every run by anything it
  recorded, plots one or several with their procedure's plots, shows a run's
  timeline and details, exports a run, and hands a plot to a notebook.
- ✅ **Reading runs from Python.** `lab_wizard.lib.data` finds runs by any
  filter and loads their points as polars frames. See
  [Reading runs back](data/database.md#reading-runs-back).
- ✅ **File saving.** Each run as a folder of CSV and YAML, on by default per
  project, laid out by the workspace's folder template (Settings). See
  [Saving files](wizard/data.md#saving-files).
- ❌ **No migrations.** The lab database refuses a file from another schema
  version rather than altering it.

## Procedures

- ✅ Definitions, the step catalog, code generation, presets, and the composer
  all work; a saved procedure always generates.
- ❌ **No wavelength sweep.** `Laser` reports its wavelength but tuning is not
  in the behavior contract, because nothing sweeps it yet.
- ❌ **No undo in the composer**, and reordering is move-up/move-down rather
  than drag and drop.

## Running measurements

- ✅ **The [Run page](wizard/measurements.md#the-run-page)** edits a project's
  device, metadata, params and outputs, runs it as its own process, and shows
  its plots and timeline live; Stop is a Ctrl-C, so the instruments end safe.
- ⚠️ **One run per project at a time**, and the page follows only runs it
  started (a run started from a terminal shows once it is recorded, as the
  project's last run).

## Server (remote control)

The server handles concurrent runs, but a few things remain:

- ✅ **Parallel dispatch** — requests are handled on worker pools, so a slow
  `set_voltage` no longer blocks unrelated calls, and two runs can interleave on
  different channels of one instrument under [run claims](remote/operations.md).
- ✅ **Graceful shutdown** releases instruments; ✅ the client reconnects.
- ❌ **No hot-reload of permissions.** Editing rules needs a server restart,
  which the GUI does for you.
- ❌ **No push notifications.** A client polls; it is not told when state it
  cares about changed.
- ❌ **No binary bulk-data path.** Returns are JSON only; large arrays should
  ride pyleco's binary payload frames.
- ⚠️ **A client without a claim can still write** to an unclaimed instrument.
  Claims keep runs off each other, they do not lock out an interactive session.

## Instruments

- ⚠️ **`AgilentN7764A` is invisible to the wizard.** Its legacy
  `AgilentN7764AConfig` has no `type` literal, no `resource_class()` and no
  `create_inst()`, so discovery cannot see it. Porting it to a
  `ChannelProvider` with `Attenuator` channels is `plans/procedure_plan.md` 6.6.
- ⚠️ **Prologix GPIB scanning** can be slow and, on some setups, report
  instruments at the wrong address due to response desync.

## How to extend cleanly

Most additions are drop-in, thanks to
[source-scanning discovery](concepts/config-and-discovery.md#type-discovery):

| To add… | Do this |
|---|---|
| A saver | A `RunSink` subclass, handed to `run_procedure(..., sinks=[...])` |
| An instrument | A `Params`/`Instrument` pair under `lib/instruments/<vendor>/`, inheriting the right KeyLike and behavior ABC |
| A behavior ABC | Add it under `lib/instruments/general/`, register a proxy in `lib/client/proxies/registry.py`, and add the steps that drive it |
| A procedure step | A runtime `Step` in `lib/task_adapters/instrument_steps.py` with its `*StepParams` schema right after it, whose field names match the step's constructor — defining the schema registers it, so the generator and the composer both pick it up with no further change |
| A measurement | Compose it in [Procedures](wizard/procedures.md), or add a YAML definition to `lib/procedures/library/` to ship it built in; for what composing cannot say, a [custom measurement](wizard/measurements.md#custom-measurements) in the workspace's `measurements/` folder |
