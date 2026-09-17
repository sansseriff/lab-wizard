---
icon: lucide/database
---

# Data

The **Data** section is where a run's output is configured and read back:
**Savers** decide where measurements are written, and **Database** browses what
has been written.

## Savers

A saver is a [flat resource](../concepts/config-and-discovery.md#flat-resources)
— no hardware addressing, no hierarchy — stored as one YAML file per configured
instance in `config/savers/`. Configure an instance here, then bind it while
[creating a measurement](measurements.md).

| Type | Class | Status |
|---|---|---|
| `database_saver` | [`DatabaseSaver`](../../lab_wizard/lib/savers/database_saver.py) | **working** — SQLite, one row per observation |
| — | `StandInSaver` | deliberate no-op, for tests and scaffolding |

There is **no file (CSV/HDF5/Parquet) saver** yet. Dropping a
`FileSaverParams`/`FileSaver` pair into `lib/savers/` would be
[discovered](../concepts/config-and-discovery.md#type-discovery) automatically.

### What a saver receives

A run publishes three kinds of message on its data bus, and
[`SaverSink`](../../lab_wizard/lib/task_adapters/savers.py) turns them into the
saver lifecycle:

| Message | Saver call | Carries |
|---|---|---|
| `RunStarted` | `start_run` | the measurement's parameters, and what each instrument was configured with |
| `Observation` | `write_measurement` | one row: the readings plus every swept value in force |
| `RunEnded` | `end_run` | the closing timestamp |

The second line is the important one: **an observation is flat**. The sweep
values in force are copied onto it, so whether the run swept bias inside trigger
level or the other way round is invisible in the stored data — which is what
lets the dataset be sliced arbitrarily afterwards.

## Database

The Database page browses the SQLite file a `database_saver` writes: runs,
their parameters, and the measurements under them.

The schema, what each table means, and how to query it by hand are in
[Measurement database](../data/database.md).
