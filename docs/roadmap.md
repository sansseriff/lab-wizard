---
icon: lucide/map
---

# Roadmap / what still needs work

An honest inventory of what is **scaffolded but not finished**, checked against
the current source. Look here before assuming a feature works end to end.

## Plotters

- ❌ **No working plotter.** Both
  [`MplPlotter`](../lab_wizard/lib/plotters/mpl_plotter.py) and
  [`BokehPlotter`](../lab_wizard/lib/plotters/bokeh_plotter.py) store the last
  payload and print; nothing renders. `StandInPlotter` is a deliberate no-op.
  The wiring around them is real — a run publishes observations and
  `PlotterSink` forwards each one — so a working plotter is two method bodies.

## Savers and data

- ✅ **`DatabaseSaver`** (SQLite) works: a `runs` row per run, a `measurements`
  row per observation, the measurement's parameters and the instruments'
  configured params both recorded. See [Measurement database](data/database.md).
- ❌ **No file saver.** No CSV / HDF5 / Parquet. A
  `FileSaverParams`/`FileSaver` pair dropped into `lib/savers/` would be
  discovered automatically.
- ⚠️ **The stored data does not say what it means.** Nothing records which
  columns are axes and which are readings, so every consumer guesses from
  names. Related: one point of a curve can span two rows (a count and a voltage
  read are separate observations), `runs.run_type` is a five-value enum that
  stores any composed procedure as `OTHER`, and `runs.device_id` is always NULL
  because no measurement passes a device. Designed in
  `plans/semantic_data_plan.md`; not built.
- ❌ **No database browser in the GUI.** `/data/database` lists the configured
  savers and says so; reading runs back means querying the file.
- ❌ **No query layer or migrations.** `lib/savers/query.py` has pandas helpers
  but pandas is not a dependency; the `measurements_full` view does not exist,
  and there is no migration framework — new nullable columns are added in place
  by `schema.add_missing_columns`.

## Procedures

- ✅ Definitions, the step catalog, code generation, presets, and the composer
  all work; a saved procedure always generates.
- ❌ **No wavelength sweep.** `Laser` reports its wavelength but tuning is not
  in the behavior contract, because nothing sweeps it yet.
- ❌ **No undo in the composer**, and reordering is move-up/move-down rather
  than drag and drop.

## Running measurements

- ❌ **The wizard cannot run a project.** It generates the folder; you run the
  setup file yourself from a terminal. There is no launch endpoint, no run view,
  and no live progress — see `plans/runner_plan.md`.

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

## Misc

- The legacy CLI [`wizard/wizard.py`](../lab_wizard/wizard/wizard.py) is an old
  interactive setup tool superseded by the GUI; it imports modules that no
  longer exist and is effectively dead.

## How to extend cleanly

Most additions are drop-in, thanks to
[source-scanning discovery](concepts/config-and-discovery.md#type-discovery):

| To add… | Do this |
|---|---|
| A saver or plotter | A `SaverParams`/`PlotterParams` subclass with a `type: Literal[...]` in `lib/savers/` or `lib/plotters/` |
| An instrument | A `Params`/`Instrument` pair under `lib/instruments/<vendor>/`, inheriting the right KeyLike and behavior ABC |
| A behavior ABC | Add it under `lib/instruments/general/`, register a proxy in `lib/client/proxies/registry.py`, and add the steps that drive it |
| A procedure step | A `*StepParams` schema in `lib/procedures/steps/` whose field names match the runtime step's constructor — the generator and the composer both pick it up with no further change |
| A measurement | Compose it in [Procedures](wizard/procedures.md), or add `lib/measurements/<name>/` with a setup template for hand-written Python |
