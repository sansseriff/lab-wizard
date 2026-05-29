---
icon: lucide/map
---

# Roadmap / what still needs work

An honest inventory of what is **scaffolded but not finished**, derived from the
current source. This is the place to look before assuming a feature works
end-to-end.

## Savers

- ✅ **`DatabaseSaver`** (SQLite) is complete and working — full schema, one row
  per integration. See [Database](data/database.md).
- ❌ **No file saver.** There is no CSV / HDF5 / Parquet saver. The
  [`saver.py`](../lab_wizard/lib/savers/saver.py) docstring and the
  [`flat_resource_io`](../lab_wizard/lib/utilities/flat_resource_io.py) examples
  both reference a file/CSV saver, but none exists. A `FileSaverParams(SaverParams)`
  + `FileSaver(GenericSaver)` dropped into `lib/savers/` would be auto-discovered.
- ❌ **No query/analysis layer.** `database_plan.md` describes a `query.py` of
  pandas helpers and a `measurements_full` SQL view; neither is implemented. No
  Alembic migrations are set up either.

## Plotters

- ❌ **No working plotter.** Both [`MplPlotter`](../lab_wizard/lib/plotters/mpl_plotter.py)
  and [`BokehPlotter`](../lab_wizard/lib/plotters/bokeh_plotter.py) are
  **placeholders** — they store the last payload and print; they do not render
  anything. `StandInPlotter` is a deliberate no-op. The `GenericPlotter` ABC
  (`plot`, `save_plot`) is in place, so a real implementation is a matter of
  filling in the bodies.

## Measurement run-logic wiring

- ⚠️ **Measurements don't yet use the savers/plotters they're given.** The wizard
  correctly plumbs `savers`/`plotters` into the generated `Resources` dataclass,
  but the measurement classes don't call `saver.start_run` / `write_measurement`
  / `end_run` or `plotter.plot`. [`iv_curve.py`](../lab_wizard/lib/measurements/iv_curve/iv_curve.py)
  also references legacy attributes that no longer exist on its `Resources`
  (`self.voltage_plotter`, `self.data_handler`, `self.params.enable_plotting`,
  `voltage_sequence()`, `set_output()`). The measurement classes need to be
  rewritten against the current `Resources` contract.
- ⚠️ **Only `iv_curve` and `pcr_curve` use the new template format.** They have
  the `# wizard:<block>:start/end` markers the generator expects. `mcr_curve`
  still uses the old `{{jinja}}`-style template and is **not** wizard-compatible.

## Server robustness (remote control)

The server works for the happy path but is missing production hardening
(see [`lib/server/`](../lab_wizard/lib/server/)):

- ❌ **Concurrency.** It's a single-threaded poll loop — one slow `set_voltage`
  blocks all RPCs. The intended contract is *same shared connection → serialized
  in arrival order; different connections → parallel*, keyed on the **root path
  segment** (the shared transport), since channels under one root share one
  serial/HTTP connection.
- ❌ **Graceful shutdown.** Stopping the server closes the socket but does not
  disconnect instruments in dependency order.
- ❌ **Client reconnect.** `Session` is a one-shot connect; no reconnect/backoff.
- ❌ **Hot-reload of permissions.** Editing rules requires a server **restart**
  (the GUI does this); there's no live `reload-permissions`.
- ❌ **Binary bulk-data path.** Returns are JSON only; large arrays (e.g. a future
  `get_counts()`) should ride pyleco's binary payload frames.

## Misc / known rough edges

- The legacy CLI [`wizard/wizard.py`](../lab_wizard/wizard/wizard.py) is an old
  interactive setup tool, superseded by the GUI; it imports modules that no
  longer exist and is effectively dead.
- Prologix GPIB bus scanning can be slow and (on some setups) report instruments
  at the wrong address due to response desync — see `.claude/prologix_scan_slowness.md`.

## How to extend cleanly

The architecture makes most additions drop-in, thanks to
[source-scanning discovery](concepts/config-and-discovery.md#type-discovery):

| To add… | Do this |
|---|---|
| A new saver/plotter | Add a `SaverParams`/`PlotterParams` subclass with a `type: Literal[...]` to `lib/savers/` or `lib/plotters/` |
| A new instrument | Add a `Params`/`Instrument` pair under `lib/instruments/<vendor>/`; inherit the right KeyLike + behavior ABC |
| A new behavior ABC | Add it under `lib/instruments/general/`, then register a proxy class in `lib/client/proxies/registry.py` for remote use |
| A new measurement | Add `lib/measurements/<name>/<name>.py` + a `_setup_template.py` with `# wizard:*` blocks and a `*Resources` dataclass |
