---
icon: lucide/database
---

# Data

The **Data** section is where a run's output is configured and read back.
Every run of a project is recorded in the workspace's
[lab database](../data/database.md) without any configuring; **Savers** are the
optional extra outputs, and **Database** is where recorded runs will be browsed.

## Savers

A saver is a [flat resource](../concepts/config-and-discovery.md#flat-resources)
— no hardware addressing, no hierarchy — stored as one YAML file per configured
instance in `config/savers/`. Configure an instance here, then bind it while
[creating a measurement](measurements.md).

| Type | Class | Does |
|---|---|---|
| `file_saver` | [`FileSaver`](../../lab_wizard/lib/savers/file_saver.py) | writes each run as a folder of CSV and YAML |
| — | `StandInSaver` | keeps the run's messages in memory, for tests |

### The file saver

For people who work with files. Each run becomes a folder:

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

Settings:

- **`path`** — where each run goes, from the same keys the Data page filters
  by: `{date}`, `{time}`, `{procedure}`, `{device}`, `{device.<property>}`,
  `{operator}`, `{run.<metadata key>}`, `{param.<path>}`, `{run_id}`. The
  default is `{date}/{procedure}_{device}_{time}`; a lab that thinks by device
  might use `{device.wafer}/{device}/{date}_{procedure}`. A folder tree can
  only be ordered one way, which is exactly what the Data page's filters are
  for. A run never overwrites another: a name already taken gets `_2`.
- **`root`** — where the folders go; empty for the workspace's `data/files`. A
  relative path is relative to the project.
- **`plot_png`** — also save the run's default plot.

The folder is a complete copy of the run's rows in the database, and the same
writer exports any recorded run (`export_run`). If the file saver fails (a full
disk, an unwritable root), it logs why and stops; the run carries on and is
still recorded in the database.

### What a saver receives

A saver is a sink on the run's messages, like the database recorder: it sees
`RunStarted`, every `Point` (one row), every step's start and end, and
`RunEnded`, in [`GenericSaver.handle`](../../lab_wizard/lib/savers/saver.py).
A new saver is a `SaverParams`/`GenericSaver` pair dropped into `lib/savers/`,
[discovered](../concepts/config-and-discovery.md#type-discovery) automatically.

## Database

Every run is recorded in the workspace's `data/lab.db`; see
[The lab database](../data/database.md) for what it holds and how to read it.

!!! warning "Browsing runs is not built yet"
    The Database page says where runs are recorded; the viewer that filters and
    plots them comes next (`plans/semantic_data_plan.md`). Until then, read runs
    from Python with `lab_wizard.lib.data`, as the database page shows.
