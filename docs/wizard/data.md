---
icon: lucide/database
---

# Data

The **Data** page is where recorded runs are browsed and plotted. Every run of a project is
recorded in the workspace's [lab database](../data/database.md) without any
configuring.

## Saving files

For people who work with files, a project can also save each run as a folder of
plain files. It is on by default, chosen per project when the
[measurement is created](measurements.md#outputs) (`outputs.files` in the
project YAML). Where the folders go and how they are named is one choice for
the whole workspace: **Saving files** on the [Settings](index.md) page, kept in
`config/data.yaml`. Each run becomes a folder:

```text
data/files/2026-09-22/mcr_curve_A7_143012/
  run.yaml          procedure, device, operator, times, status, params,
                    instrument settings, columns with units
  procedure.yaml    the procedure definition it ran
  points.csv        one line per point: seq, t, every column, the steps that recorded it
  steps.csv         every step it executed, with times and status
  hist.csv          one file per array column (a histogram): seq, then a column per bin
  plot.png          the run's default plot (optional)
```

`points.csv` has the same rows as the database: a point's count and voltage on
one line, an empty cell for anything not recorded at that point. It is written a
line at a time while the run goes, so a crash loses at most one line.

Settings, in `config/data.yaml` under `files:`:

- **`path`** — where each run goes, from the same keys the Data page filters
  by: `{date}`, `{time}`, `{procedure}`, `{device}`, `{device.<property>}`,
  `{operator}`, `{setup}`, `{setup.<field>}`, `{param.<path>}`, `{run_id}`. The
  default is `{date}/{procedure}_{device}_{time}`; a lab that thinks by device
  might use `{device.wafer}/{device}/{date}_{procedure}`. A folder tree can
  only be ordered one way, which is exactly what the Data page's filters are
  for. A run never overwrites another: a name already taken gets `_2`.
- **`root`** — where the folders go; empty for the workspace's `data/files`. A
  relative path is relative to the workspace.
- **`plot_png`** — also save the run's default plot.

A change applies to every project's next run; nothing is regenerated.

The folder is a complete copy of the run's rows in the database, and the same
writer exports any recorded run (`export_run`). If the
[file saver](../../lab_wizard/lib/savers/file_saver.py) fails (a full disk, an
unwritable root), it logs why and stops; the run carries on and is still
recorded in the database.

### Writing another saver

A saver is a [`RunSink`](../../lab_wizard/lib/task_adapters/sinks.py): its
`handle(message, run)` sees `RunStarted`, every `Point` (one row), every step's
start and end, and `RunEnded`, each after the database has recorded it, so
`run.run_id` is already set when it sees the run start. One written by hand is
passed to `run_procedure(..., sinks=[...])`, which replaces the ones the
project's `outputs:` asks for.

## Database

Every run in the workspace, whichever project it came from, filtered by what
it was and plotted by what it recorded. Runs are recorded in the workspace's
`data/lab.db`; see [The lab database](../data/database.md) for what it holds
and how to read it from Python.

The page has three panes.

**Filters**, on the left. Every fact a run recorded is a filter: its
procedure, device, and the device's properties; its setup and the setup's
fields as the run copied them (such as `setup.cryostat`; a quantity is
compared in its base unit, so 100 kΩ and 100000 Ω are one value); every
instrument's type and
settings; every param; the operator, date and status; and the columns it
recorded. Nothing is configured: a filter appears the first time a run records
the fact behind it. Each value shows how many runs it would leave, and a key's
counts ignore that key's own choice, so choosing one procedure still shows the
others. A numeric filter with many values offers a range instead. Search finds
a filter by its name or by a value.

**Runs**, in the middle, newest first. Click one to look at it. Cmd/Ctrl-click
adds or removes a run, and Shift-click takes a range; several chosen runs
overlay on one plot, a line each. A run still being recorded is marked
**running**, and the page follows it as it grows.

**The chosen run**, on the right, has three tabs:

- **Plot** draws the run with the plots it was recorded with, one tab each,
  and its derived columns as they were when it ran. However its procedure has
  changed since, or whether it still exists, a past run draws the same.
    - **Edit plot** changes the axes, which rows are drawn, one line per what,
      and the scales. An axis can be any expression of the columns:
      `count_rate / 1000`, or
      `counts - mean(counts, phase == "background")`. See
      [derived columns](procedures.md).
    - **Save to procedure** adds the plot to the procedure, for the runs it
      records from then on. Saving to a built-in procedure makes this
      workspace's own copy of it.
    - **Open in notebook** gives the Python that draws the same plot from the
      database with matplotlib: the place for what this page does not do,
      such as fits and arithmetic between runs.
    - Click a point to see everything recorded with it and the steps that
      recorded it.
- **Timeline** shows every step the run executed, as a bar on the run's time
  axis, nested as the procedure nests them. The steps behind a clicked point
  are highlighted.
- **Details** lists the run's setup as it was when the run started (pictures
  included) and what its procedure read from it, its params, each
  instrument's settings as the run started, and its columns with their units.

**Export run** downloads the run as a zipped folder, laid out exactly as the
[file saver](#saving-files) writes one.

**Devices** is where a device's properties live, such as its wafer, width or
type. A run names only its device, so a property set here becomes a filter on
every run of that device, past ones included. A device can be registered
before it is ever measured.
