# Lab Measurement Database: Design and Implementation Plan

> Companion document: **Task Tree Framework: Design and Implementation Plan**. This
> document covers *storage*; that one covers *acquisition*. They are deliberately
> decoupled — the framework emits measurements, this schema stores them, and
> neither knows the other's internal structure.

## Background and Motivation

The lab software historically used an Action framework — a hierarchical task system where Action objects exposed an `.evaluate()` method called from the main event loop — and a saving system that mirrored that hierarchy as nested JSON.

The redesign motivation: lab work is moving toward measurements where the *execution* hierarchy is convenient (e.g., `measure_at_thermal_power(3.0, submeasurement=measure_trigger_levels(...))`) but the *data* is fundamentally flat — each integration produces one observation tagged with the conditions under which it was taken. The order of inner vs outer loops should not change the resulting dataset, and analysis often wants to slice by parameters that were not the "outer" loop variable.

**Key insight: execution structure and storage structure should be decoupled.** The task hierarchy generates the measurement schedule; the data itself is a set of observations with metadata.

## The Core Data Model

Each integration produces one row. A row carries:

- The measured quantities (counts, voltage, current, field, attenuation, ...)
- The conditions/parameters set when it was measured (bias current, trigger level, thermal power, ...)
- Acquisition-position metadata (sequence order, sweep index)
- Bookkeeping (timestamp, run, device, cryostat, temperature)

Whether the inner loop was `thermal_power` or `trigger_level` is invisible in the data, which is what allows arbitrary post-hoc slicing.

## Mental Models

**Tables are types, not events.** A table is a *kind of thing* (measurements, runs, devices); rows are instances. The table as a whole is not the unit of useful data — filtering and joining produce useful data. A `measurements` table with millions of rows accumulating over months is normal and good: a uniform substrate that can answer questions not anticipated at collection time.

**One row = one integration.** A row in `measurements` is the atomic thing produced by a single integration step. A PCR curve sweeping 20 trigger levels at 5 thermal powers produces 100 rows. The "curve" is a query result, not a stored object.

**Each fact lives in exactly one place.** "Run 42 was on device A7" is a property of the run, so `device_id` lives on `runs`, not on every measurement. Redundant storage creates the risk of inconsistent answers with no authority. Joins retrieve related data; views hide the join syntax for convenience.

**Acquisition structure is metadata, not schema.** A measurement's *position* in the acquisition process (global order, which sweep pass) is recorded as flat, indexed scalar tags — never as a nested storage tree. Flat position tags let any grouping be reconstructed by query while keeping every row atomic. (Detailed below.)

## Wide Table vs Flexible Representation

A central tension in scientific schema design: a **wide sparse table** (many typed columns, most NULL per row) versus a **flexible representation** (key-value/EAV or JSON). The honest assessment:

- **Wide sparse table** — default in practice. NULLs are nearly free in SQLite; every column typed, indexable, directly queryable. Fails only at hundreds of columns, which a single lab never reaches. Aesthetic discomfort, not a real problem at this scale.
- **Entity-Attribute-Value** (one row per `(measurement_id, quantity, value, unit)`) — maximally flexible, and the option experienced practitioners warn about most. Permanent query-and-analysis tax: self-joins per quantity, no type safety, constant pivoting, no constraints. Justified only when the attribute set is genuinely unbounded and unknowable. A physics lab's measured quantities are neither.
- **JSON column** — the pragmatic middle, and what this design uses. Flexibility of EAV, most of the query convenience of columns (SQLite `json_extract`, indexable expressions), at the cost of slightly more verbose queries and no in-JSON type enforcement.

**Decision: hybrid.** A few promoted typed columns for things present on nearly every measurement *and* frequently filtered (`temperature`, acquisition indices, foreign keys, timestamp); a `data` JSON column for measured quantities that vary by experiment type; a `metadata` JSON column for conditions/parameters; a `measurement_details` table for genuine multi-row sub-structure (histograms, time series within one integration).

**Promotion rule:** promote a JSON field to a typed column based on observed query patterns, not on a desire for purity. Start with almost everything in JSON; promote the two or three quantities that prove hot. The painful migration is the over-engineered one, not the under-engineered one. Resist the "fully generic measurement framework" trap — maximal generality is paid for on *every query for the system's lifetime* and buys almost nothing because the lab's quantity set is small and slowly-changing.

## Acquisition-Position Metadata (Sweeps and Drift)

The "loop order shouldn't change the dataset" principle has a refinement: loop *order* sometimes changes the *physics*. A voltage sweep collected as ten fast passes over an hour averages apparatus heating across all points; the same sweep collected as one slow pass shows heating only in later points. The acquisition *position* of each measurement is therefore real physical information and must be preserved — as flat metadata, never as hierarchy.

Two promoted columns capture this:

- `sequence_index` — monotonic global acquisition order within the run (0, 1, 2, ...). Authoritative and gap-free; owned by the writer, not by any task.
- `sweep_index` — which pass over a swept parameter this measurement belongs to (NULL when not part of a sweep).

These meet the promotion bar (present on essentially every measurement, constantly grouped/filtered on). With them, every grouping is a query, not a schema commitment:

- Heat-averaged curve: `GROUP BY voltage`, average over `sweep_index`.
- Per-sweep curves (drift visible): `GROUP BY sweep_index`.
- Single-long-sweep drift: plot rate vs `sequence_index`, color by `temperature`.
- A grouping not yet imagined: still recoverable, because atomic facts and acquisition order are both preserved.

A `sweeps` table between `runs` and `measurements` is deliberately **rejected**: it bakes one grouping into the schema, defeating the reason flat storage was chosen.

## Schema

Six tables forming a tree: `wafers → devices → runs → measurements → measurement_details`, with `cryostats` off `runs`.

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
    name = Column(String, unique=True, nullable=False)   # "W2026-03"
    fabrication_date = Column(DateTime)
    material = Column(String)                              # "WSi", "NbN"
    notes = Column(String)


class Device(Base):
    __tablename__ = "devices"
    id = Column(Integer, primary_key=True)
    wafer_id = Column(Integer, ForeignKey("wafers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)                 # "W2026-03-A7"
    pixel_geometry = Column(String)                        # "meander", "spiral"
    width_nm = Column(Float)
    length_um = Column(Float)
    metadata_json = Column("metadata", JSON)               # other per-device features

    wafer = relationship("Wafer")


class Cryostat(Base):
    __tablename__ = "cryostats"
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)     # "BlueFors-1"
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
    config = Column(JSON)        # serialized task tree, software git SHA, calibration

    cryostat = relationship("Cryostat")
    device = relationship("Device")
    measurements = relationship("Measurement", back_populates="run",
                                cascade="all, delete-orphan")


class Measurement(Base):
    __tablename__ = "measurements"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Acquisition position — promoted because always present and always grouped on
    sequence_index = Column(Integer, nullable=False)   # global acq order in run
    sweep_index = Column(Integer)                       # which pass; NULL if N/A

    # Promoted: present on ~every measurement and frequently filtered
    temperature = Column(Float)                          # sample stage, K

    # Flexible measured quantities — counts, voltage, current, field, ...
    data = Column(JSON)

    # Flexible conditions/parameters — bias_current, trigger_level, thermal_power
    metadata_json = Column("metadata", JSON)

    run = relationship("Run", back_populates="measurements")
    details = relationship("MeasurementDetail", back_populates="measurement",
                           cascade="all, delete-orphan")


class MeasurementDetail(Base):
    __tablename__ = "measurement_details"
    id = Column(Integer, primary_key=True)
    measurement_id = Column(Integer, ForeignKey("measurements.id"),
                            nullable=False, index=True)
    detail_type = Column(String, nullable=False)  # "histogram_bin", "time_window"
    bin_index = Column(Integer)
    bin_value = Column(Float)                       # bin center / window start
    value = Column(Float, nullable=False)

    measurement = relationship("Measurement", back_populates="details")


Index("idx_runs_cryostat_started", Run.cryostat_id, Run.started_at)
Index("idx_runs_type", Run.run_type)
Index("idx_measurements_timestamp", Measurement.timestamp)
Index("idx_measurements_run_seq", Measurement.run_id, Measurement.sequence_index)
```

### Schema Notes

- **`cryostats` / `wafers` as tables** prevent typos splitting data and let per-entity metadata live in one place.
- **`run_type` as Enum** — soft constraint catching typos; new types are one line.
- **`config` JSON on runs** holds per-run constants (serialized task tree, git SHA, calibration file). Rarely queried into; reproducibility record.
- **`data` JSON on measurements** holds measured quantities; new experiment types add keys freely. Index hot paths: `CREATE INDEX idx_voltage ON measurements(json_extract(data,'$.voltage'))`.
- **`metadata` JSON on measurements** holds conditions/parameters.
- **`temperature`, `sequence_index`, `sweep_index` promoted** to typed indexed columns per the promotion rule.
- **`measurement_details`** is the one place a child table genuinely earns its keep — inherently multi-row-per-measurement and queried into.
- **`device_id` on runs, not measurements** — one run is one device; "all measurements on device X" is a fast indexed join. Add `device_id` to `measurements` only if a single run can measure multiple devices.

## Connecting to the Task Tree Framework

The framework (see companion document) does not write to the database directly. It **emits typed messages** onto a bus; the database writer is a *subscriber* to that bus. This keeps the framework ignorant of storage and storage ignorant of acquisition.

```python
from datetime import datetime
from sqlalchemy.orm import Session


class DatabaseSink:
    """Subscribes to the framework message bus; persists measurements.

    One commit per completed measurement => every finished integration is
    durable on disk before the next begins (crash-safe)."""

    def __init__(self, db_path, cryostat_name):
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.cryostat = self._get_or_create_cryostat(cryostat_name)
        self.current_run = None
        self._seq = 0

    def _get_or_create_cryostat(self, name):
        c = self.session.query(Cryostat).filter_by(name=name).first()
        if not c:
            c = Cryostat(name=name)
            self.session.add(c); self.session.commit()
        return c

    # --- message handlers (one per message type on the bus) ---

    def on_run_started(self, msg):
        self.current_run = Run(
            cryostat_id=self.cryostat.id,
            device_id=msg.device_id,
            run_type=msg.run_type,
            operator=msg.operator,
            description=msg.description,
            config=msg.config,
        )
        self.session.add(self.current_run)
        self.session.commit()
        self._seq = 0

    def on_measurement_completed(self, msg):
        m = Measurement(
            run_id=self.current_run.id,
            sequence_index=self._next_seq(),
            sweep_index=msg.sweep_index,
            temperature=msg.temperature,
            data=msg.data,
            metadata_json=msg.metadata,
        )
        self.session.add(m)
        self.session.flush()                 # assign m.id
        for d in (msg.details or []):
            self.session.add(MeasurementDetail(measurement_id=m.id, **d))
        self.session.commit()                # durable now

    def on_run_ended(self, msg):
        self.current_run.ended_at = datetime.utcnow()
        self.session.commit()
        self.current_run = None

    def _next_seq(self):
        i = self._seq; self._seq += 1
        return i
```

The framework guarantees the message contract (field names of `msg`); the schema guarantees storage. Changing either side's internals does not break the other as long as the message contract holds.

## Reading the Data

A `query.py` module wraps common slices so notebooks never write raw SQL:

```python
import pandas as pd, json
from sqlalchemy import create_engine


def get_measurements(db_path, run_id):
    """All measurements in a run; data + metadata expanded into columns."""
    engine = create_engine(f"sqlite:///{db_path}")
    df = pd.read_sql("""
        SELECT id, timestamp, sequence_index, sweep_index, temperature,
               data, metadata
        FROM measurements WHERE run_id = ? ORDER BY sequence_index
    """, engine, params=(run_id,))
    data = pd.json_normalize(df["data"].apply(json.loads))
    meta = pd.json_normalize(df["metadata"].apply(json.loads))
    return pd.concat([df.drop(["data", "metadata"], axis=1), data, meta], axis=1)


def get_sweeps(db_path, run_id):
    """Per-sweep view for drift analysis. Group by sweep_index for individual
    passes, or by the swept parameter for the heat-averaged curve."""
    df = get_measurements(db_path, run_id)
    df["rate"] = df["counts"] / df["delta_time"]
    return df


def get_all_measurements_for_device(db_path, device_name):
    engine = create_engine(f"sqlite:///{db_path}")
    return pd.read_sql("""
        SELECT m.*, r.run_type, r.started_at AS run_started
        FROM measurements m
        JOIN runs r   ON m.run_id = r.id
        JOIN devices d ON r.device_id = d.id
        WHERE d.name = ? ORDER BY m.timestamp
    """, engine, params=(device_name,))


def get_histogram(db_path, measurement_id):
    engine = create_engine(f"sqlite:///{db_path}")
    return pd.read_sql("""
        SELECT bin_index, bin_value, value FROM measurement_details
        WHERE measurement_id = ? AND detail_type = 'histogram_bin'
        ORDER BY bin_index
    """, engine, params=(measurement_id,))
```

### Convenience View

```sql
CREATE VIEW measurements_full AS
SELECT m.id, m.timestamp, m.sequence_index, m.sweep_index, m.temperature,
       m.data, m.metadata,
       r.id AS run_id, r.run_type, r.started_at AS run_started,
       c.name AS cryostat,
       d.name AS device, d.pixel_geometry, d.width_nm,
       w.name AS wafer, w.material
FROM measurements m
JOIN runs r      ON m.run_id = r.id
JOIN cryostats c ON r.cryostat_id = c.id
JOIN devices d   ON r.device_id = d.id
JOIN wafers w    ON d.wafer_id = w.id;
```

`SELECT * FROM measurements_full WHERE wafer='W2026-03' AND temperature<2.5` works with no hand-written joins. The view is virtual — no duplicated data.

## Operational Setup

- **One shared SQLite file** on a lab server / network location. Each cryostat program opens, writes, closes; SQLite locking coordinates. Split into per-cryostat files only if write contention becomes real (it will not at a dozen runs/day).
- **Backups:** daily `cp measurements.db backups/measurements_$(date +%F).db`; or SQLite's online backup API for mid-write safety.
- **Migrations:** install Alembic day one. Promotion migration pattern:
  ```sql
  ALTER TABLE measurements ADD COLUMN voltage REAL;
  UPDATE measurements SET voltage = json_extract(data, '$.voltage');
  CREATE INDEX idx_voltage ON measurements(voltage);
  ```
  then update the sink to populate the new column going forward.
- **Validation habit:** before any non-trivial migration, capture an invariant (`SELECT SUM(...) ...`), run before and after, confirm equality.

## When to Revisit

- **Promote a JSON field** when filtering on it is constant and queries feel awkward, or JSON queries become a performance issue.
- **Add `device_id` to `measurements`** only if one run can measure multiple devices.
- **Switch to Postgres** only with concurrent multi-machine writes or >~100 GB.
- **Split databases** only if backup/copy time or write contention becomes real.

## Summary

Six tables, indexed integer foreign keys forming a tree, JSON columns for flexible measured quantities and conditions, a few promoted typed columns chosen by query patterns, acquisition position preserved as flat indexed metadata. Writes arrive via a message-bus sink (one commit per integration, crash-safe). Analysis goes through a thin `query.py`. The schema knows nothing about how data was acquired; the framework knows nothing about how it is stored. The seam between them is a typed message contract.
