# Lab Measurement Database: Design and Implementation Plan

## Background and Motivation

The existing lab software uses an Action framework — a hierarchical task system where Action objects expose an `.evaluate()` method called from the main event loop. Actions can contain other actions, and complex behaviors like coarse scans and entanglement visibility searches are composed by nesting Actions inside Actions. The existing saving system mirrors this hierarchy: results are flattened from nested Action structures into JSON files.

The motivation for redesigning the saving system: lab work is moving toward measurements where the _execution_ hierarchy is convenient (e.g., `measure_at_thermal_power(3.0, submeasurement=measure_trigger_levels(...))`) but the _data_ is fundamentally flat — each integration produces one observation tagged with the conditions under which it was taken. The order of inner vs outer loops shouldn't affect the resulting dataset, and analysis often wants to slice the data by parameters that weren't the "outer" loop variable.

The key insight: **execution structure and storage structure should be decoupled**. The action hierarchy generates the measurement schedule; the data itself is a set of observations with metadata.

## The Core Data Model

Each integration produces one row. Columns include:

- The measured values (counts, count rate, integration time, temperature)
- Every parameter that was set when the measurement happened (bias current, trigger level, thermal power, voltage)
- Bookkeeping (timestamp, run identifier, device, cryostat)

Example PCR sweep across thermal powers and trigger levels:

```
run_id | timestamp           | thermal_power | trigger_level | bias_current | counts | int_time
-------|---------------------|---------------|---------------|--------------|--------|----------
42     | 2026-05-07 10:23:01 | 3.0           | 0.034         | 12.5         | 14823  | 1.0
42     | 2026-05-07 10:23:03 | 3.0           | 0.035         | 12.5         | 13201  | 1.0
42     | 2026-05-07 10:23:15 | 5.0           | 0.034         | 12.5         | 28104  | 1.0
```

Whether the inner loop was `thermal_power` or `trigger_level` is invisible in the data — which is what allows arbitrary post-hoc slicing.

## Mental Models That Made This Click

**Tables are types, not events.** A table is a _kind of thing_ (measurements, runs, devices) and rows are instances of that kind. The table itself isn't the unit of useful data — useful data comes from filtering/joining tables to answer specific questions. A `measurements` table with millions of rows accumulating over months is normal and good; its value is as a uniform substrate that can answer questions you didn't anticipate when the data was collected.

**One row = one integration.** A row in `measurements` is the atomic thing produced by a single `Integrate` action. A PCR curve sweeping 20 trigger levels at 5 thermal powers produces 100 rows. The "scan" or "curve" doesn't exist as a stored object — it's a query result.

**Each fact lives in exactly one place.** This is the principle that determines what goes where. The fact "run 42 was on device A7" is a property of the run, so device_id lives on `runs`, not on each measurement. Storing it redundantly creates the danger of inconsistent answers and no way to know which is right. Joins retrieve the related data when needed; views can hide the join syntax for convenience.

## Choice of Database

**SQLite, not Postgres.** For a dozen runs/day across multiple cryostats:

- SQLite handles databases up to terabytes
- Concurrent readers are fine; writes serialize but each cryostat owns its own data
- One file means trivial backups (`cp measurements.db backup.db`)
- No server to maintain
- SQLAlchemy abstracts over SQLite/Postgres so future migration is one connection-string change

The threshold where Postgres becomes necessary: multiple programs writing simultaneously to the same database, network access from many machines, fine-grained user permissions. None apply here.

**Format for the data:** SQLite over Parquet/HDF5 because it supports incremental writes during long measurements (each completed integration is durable on disk before the next starts) — a real liability mitigation for lab work where power blips, bugs in new Actions, or accidental Ctrl-C can occur.

## Schema

Six tables forming a hierarchy: `wafers → devices → runs → measurements → measurement_details`, with `cryostats` hanging off `runs`.

```python
from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime,
    ForeignKey, JSON, Index, Enum
)
from sqlalchemy.orm import declarative_base, relationship, Session
from datetime import datetime
import enum

Base = declarative_base()


class RunType(enum.Enum):
    PCR_CURVE = "pcr_curve"
    IV_CURVE = "iv_curve"
    EXTENDED_PCR = "extended_pcr"
    # add more as the lab develops new measurement types


class Wafer(Base):
    __tablename__ = "wafers"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)  # "W2026-03"
    fabrication_date = Column(DateTime)
    material = Column(String)  # "WSi", "NbN", etc.
    notes = Column(String)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    wafer_id = Column(Integer, ForeignKey("wafers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # "W2026-03-A7" or just "A7"
    pixel_geometry = Column(String)  # "meander", "spiral", etc.
    width_nm = Column(Float)
    length_um = Column(Float)
    metadata_json = Column("metadata", JSON)  # other per-device features

    wafer = relationship("Wafer")


class Cryostat(Base):
    __tablename__ = "cryostats"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)  # "BlueFors-1"
    location = Column(String)
    notes = Column(String)


class Run(Base):
    __tablename__ = "runs"
    id = Column(Integer, primary_key=True)
    cryostat_id = Column(Integer, ForeignKey("cryostats.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=False, index=True)
    run_type = Column(Enum(RunType), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime)
    operator = Column(String)
    description = Column(String)
    config = Column(JSON)  # action tree, software version, calibration constants

    cryostat = relationship("Cryostat")
    device = relationship("Device")
    measurements = relationship("Measurement", back_populates="run",
                                cascade="all, delete-orphan")


class Measurement(Base):
    __tablename__ = "measurements"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Common observables across all measurement types
    counts = Column(Integer)
    int_time = Column(Float)        # requested integration time
    delta_time = Column(Float)      # actual elapsed time
    temperature = Column(Float)     # sample stage temperature, K

    # Varying parameters: bias_current, trigger_level, thermal_power, voltage, etc.
    metadata_json = Column("metadata", JSON)

    run = relationship("Run", back_populates="measurements")
    details = relationship("MeasurementDetail", back_populates="measurement",
                          cascade="all, delete-orphan")


class MeasurementDetail(Base):
    __tablename__ = "measurement_details"
    id = Column(Integer, primary_key=True)
    measurement_id = Column(Integer, ForeignKey("measurements.id"),
                           nullable=False, index=True)
    detail_type = Column(String, nullable=False)  # "histogram_bin", "time_window", etc.
    bin_index = Column(Integer)
    bin_value = Column(Float)   # bin center, or window start time
    value = Column(Float, nullable=False)

    measurement = relationship("Measurement", back_populates="details")


# Indexes for common query patterns
Index("idx_runs_cryostat_started", Run.cryostat_id, Run.started_at)
Index("idx_runs_type", Run.run_type)
Index("idx_measurements_timestamp", Measurement.timestamp)
```

### Schema Design Notes

- **`cryostats` and `wafers` as separate tables** prevent typos (`"BlueFors1"` vs `"BlueFors-1"`) from splitting data, and let metadata about each cryostat or wafer live in one place instead of being duplicated.
- **`run_type` as Enum** is a soft constraint that catches typos and makes the set of valid types explicit. Adding new types is a one-line code change.
- **`config` JSON on runs** holds per-run constants: the action tree, git SHA, calibration file, constant bias current. Rarely queried into; mostly for reproducibility.
- **`metadata` JSON on measurements** holds varying parameters. The schema doesn't need to know whether a particular run has `thermal_power` vs `voltage_set`. If a parameter becomes important enough to query frequently, promote it to a real indexed column later (this is an easy migration).
- **`temperature` as a real column** rather than JSON because it's present for every measurement and will frequently be filtered on. If multiple stage temperatures matter, either add multiple columns (`temp_mixing_chamber`, `temp_4k`) or use a separate `temperature_log` table.
- **`measurement_details` is generic** — it can hold histograms, time-windowed counts, per-channel data. The `detail_type` column distinguishes them. Use this table when sub-structure is queried into; use JSON in `metadata` when sub-structure is always read as a unit.
- **`device_id` on runs, not measurements.** Each run is one device, so device is a per-run property. Querying "all measurements on device A7" uses a join across `measurements → runs → devices`, which is fast on indexed integer foreign keys.

## Connecting to the Action Framework

Each Action that sets a parameter (`SetVoltage`, `SetBias`, `SetThermalPower`, `SetTriggerLevel`) updates a shared **context dict**. When `Integrate` finishes, it snapshots the context and writes one row.

```python
class DatabaseWriter:
    def __init__(self, db_path, cryostat_name):
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

        self.cryostat = self.session.query(Cryostat).filter_by(
            name=cryostat_name).first()
        if not self.cryostat:
            self.cryostat = Cryostat(name=cryostat_name)
            self.session.add(self.cryostat)
            self.session.commit()

        self.current_run = None

    def start_run(self, run_type, device_id, operator, description, config):
        self.current_run = Run(
            cryostat_id=self.cryostat.id,
            device_id=device_id,
            run_type=run_type,
            operator=operator,
            description=description,
            config=config,
        )
        self.session.add(self.current_run)
        self.session.commit()
        return self.current_run.id

    def write_measurement(self, counts, int_time, delta_time,
                          metadata, temperature=None, details=None):
        m = Measurement(
            run_id=self.current_run.id,
            counts=counts,
            int_time=int_time,
            delta_time=delta_time,
            temperature=temperature,
            metadata_json=metadata,
        )
        self.session.add(m)
        self.session.flush()  # assign m.id without committing yet

        if details:
            for d in details:
                self.session.add(MeasurementDetail(
                    measurement_id=m.id, **d))

        self.session.commit()  # now durable on disk

    def end_run(self):
        self.current_run.ended_at = datetime.utcnow()
        self.session.commit()
        self.current_run = None
```

### Modifying the Action Framework

The base `Action` class gains awareness of a shared experiment state object (or context dict) that's passed down through `evaluate()` calls. Parameter-setting Actions update it on entry; `Integrate` reads it and passes it as `metadata` to the writer.

```python
class Integrate(Action):
    def evaluate(self, current_time, counts, context, writer, **kwargs):
        # ... existing integration logic ...
        if done:
            writer.write_measurement(
                counts=self.counts,
                int_time=self.int_time,
                delta_time=self.delta_time,
                metadata=dict(context),  # snapshot
                temperature=context.get("_temperature"),  # if cryostat exposes it
            )
            return {"state": "finished", ...}
```

For extended PCR with histograms, `Integrate` also assembles a list of detail dicts:

```python
details = [
    {"detail_type": "histogram_bin", "bin_index": i,
     "bin_value": bin_centers[i], "value": hist[i]}
    for i in range(len(hist))
]
writer.write_measurement(counts=total, int_time=1.0, delta_time=1.02,
                         metadata={"thermal_power": 3.0, "bias_current": 12.5},
                         temperature=2.13, details=details)
```

## Reading the Data

A `query.py` module wraps common slicing operations so analysis notebooks don't write raw SQL:

```python
import pandas as pd
import json
from sqlalchemy import create_engine

def get_measurements(db_path, run_id):
    """All measurements in a run, with metadata expanded into columns."""
    engine = create_engine(f"sqlite:///{db_path}")
    df = pd.read_sql("""
        SELECT id, timestamp, counts, int_time, delta_time, temperature, metadata
        FROM measurements
        WHERE run_id = ?
        ORDER BY timestamp
    """, engine, params=(run_id,))
    metadata_df = pd.json_normalize(df["metadata"].apply(json.loads))
    return pd.concat([df.drop("metadata", axis=1), metadata_df], axis=1)


def get_runs(db_path, cryostat_name=None, run_type=None,
             device_name=None, since=None):
    """Filter runs by various criteria, joining in cryostat/device names."""
    engine = create_engine(f"sqlite:///{db_path}")
    query = """
        SELECT r.id, r.run_type, r.started_at, r.ended_at, r.operator,
               r.description, c.name as cryostat, d.name as device, w.name as wafer
        FROM runs r
        JOIN cryostats c ON r.cryostat_id = c.id
        JOIN devices d ON r.device_id = d.id
        JOIN wafers w ON d.wafer_id = w.id
        WHERE 1=1
    """
    params = []
    if cryostat_name:
        query += " AND c.name = ?"; params.append(cryostat_name)
    if run_type:
        query += " AND r.run_type = ?"; params.append(run_type)
    if device_name:
        query += " AND d.name = ?"; params.append(device_name)
    if since:
        query += " AND r.started_at >= ?"; params.append(since)
    query += " ORDER BY r.started_at DESC"
    return pd.read_sql(query, engine, params=params)


def get_histogram(db_path, measurement_id):
    """Pull histogram bins for a single measurement."""
    engine = create_engine(f"sqlite:///{db_path}")
    return pd.read_sql("""
        SELECT bin_index, bin_value, value
        FROM measurement_details
        WHERE measurement_id = ? AND detail_type = 'histogram_bin'
        ORDER BY bin_index
    """, engine, params=(measurement_id,))


def get_all_measurements_for_device(db_path, device_name):
    """All integrations across all runs that used a particular device."""
    engine = create_engine(f"sqlite:///{db_path}")
    return pd.read_sql("""
        SELECT m.*, r.run_type, r.started_at as run_started
        FROM measurements m
        JOIN runs r ON m.run_id = r.id
        JOIN devices d ON r.device_id = d.id
        WHERE d.name = ?
        ORDER BY m.timestamp
    """, engine, params=(device_name,))
```

### Convenience: a SQL view

Since "measurements joined to run, device, wafer, cryostat metadata" is the most common analysis shape, define it once as a view:

```sql
CREATE VIEW measurements_full AS
SELECT
    m.id, m.timestamp, m.counts, m.int_time, m.delta_time, m.temperature,
    m.metadata,
    r.id as run_id, r.run_type, r.started_at as run_started,
    c.name as cryostat,
    d.name as device, d.pixel_geometry, d.width_nm,
    w.name as wafer, w.material
FROM measurements m
JOIN runs r ON m.run_id = r.id
JOIN cryostats c ON r.cryostat_id = c.id
JOIN devices d ON r.device_id = d.id
JOIN wafers w ON d.wafer_id = w.id;
```

Now `SELECT * FROM measurements_full WHERE wafer = 'W2026-03' AND temperature < 2.5` works without writing the joins each time. The view is virtual — no duplicate data, the database does the joins under the hood.

## Operational Setup

**One shared database file** on a network location or lab server. Each cryostat's measurement program opens it, writes its rows, closes. SQLite's locking handles coordination; write contention is rare since integrations take seconds.

If write contention ever becomes a problem (it won't, given throughput), splitting into per-cryostat files is straightforward — but cross-cryostat queries become harder, so don't do it preemptively.

**Backups.** SQLite is one file. Daily cron job: `cp measurements.db backups/measurements_$(date +%Y-%m-%d).db`. For extra safety against backups taken mid-write, use SQLite's online backup API (one Python call) which is safe during concurrent writes.

**Schema migrations.** Install Alembic from day one (`pip install alembic`, `alembic init`). When promoting a JSON metadata field to a real column, or adding a new table, write a numbered migration script and run `alembic upgrade head`. Costs nothing now, saves real pain later.

**Validation habit.** Before any non-trivial migration, write a query capturing some invariant (e.g., `SELECT SUM(counts) FROM measurements WHERE run_id = 42`) and run it before and after the migration. Confirms nothing was lost or corrupted.

## When to Revisit Decisions

**Promote a JSON field to a real column** when you find yourself filtering on it constantly and the queries feel awkward, or when JSON queries become a noticeable performance issue. Migration:

```sql
ALTER TABLE measurements ADD COLUMN thermal_power REAL;
UPDATE measurements SET thermal_power = json_extract(metadata, '$.thermal_power');
CREATE INDEX idx_thermal ON measurements(thermal_power);
```

Then update writer code to populate the new column going forward.

**Add `device_id` to `measurements`** only if a single run can measure multiple devices (e.g., comparison experiments under identical conditions). Otherwise the join through `runs` is correct.

**Switch to Postgres** if multiple machines need concurrent write access, or if the database grows beyond ~100 GB and you need better query optimization. Migration is mostly a connection-string change in SQLAlchemy plus dumping/loading the data.

**Split databases** (per-cryostat or per-year) only if backup/copy times become unwieldy or write contention becomes real.

## Summary

Six tables, indexed integer foreign keys forming a tree, JSON columns for flexible metadata, real columns for things you query frequently. Incremental writes via SQLAlchemy with one commit per completed integration. Analysis goes through a thin `query.py` module that hides SQL behind useful functions. Backups are file copies. Migrations are versioned with Alembic. Total implementation: a few hundred lines of Python.

The framework cleanly separates execution (the existing Action hierarchy) from storage (the flat-with-metadata tables). New experiment types add new run types and possibly new metadata fields, but don't require schema changes. Cross-experiment analysis becomes possible because all measurements live in the same uniform substrate, queryable by any combination of conditions.
