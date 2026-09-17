from __future__ import annotations

"""
SQLAlchemy schema for the lab measurement database.

Six tables forming a hierarchy: wafers → devices → runs → measurements →
measurement_details, with cryostats hanging off runs.

See database_plan.md (root of the repo) for the design rationale.
"""

import enum
from datetime import datetime

from sqlalchemy import (
    Column, Integer, Float, String, DateTime, ForeignKey, JSON, Index, Enum,
)
from sqlalchemy.orm import declarative_base, relationship


Base = declarative_base()


def add_missing_columns(engine) -> list[str]:
    """Add columns this schema has and an existing database does not.

    There is no migration framework here (see ``plans/database_plan.md``), and
    a database written by an older lab_wizard is a normal thing to open: a run
    recorded last month must still be readable, and a new run must still be
    writable beside it. Only nullable columns can be added this way, which is
    what every column added so far has been; anything else needs a real
    migration and is left alone deliberately.
    """
    from sqlalchemy import inspect

    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue  # create_all makes it, with every column
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing or not column.nullable:
                    continue
                type_sql = column.type.compile(engine.dialect)
                connection.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.name} {type_sql}"
                )
                added.append(f"{table.name}.{column.name}")
    return added


class RunType(str, enum.Enum):
    PCR_CURVE = "pcr_curve"
    IV_CURVE = "iv_curve"
    MCR_CURVE = "mcr_curve"
    EXTENDED_PCR = "extended_pcr"
    OTHER = "other"


class Wafer(Base):
    __tablename__ = "wafers"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    fabrication_date = Column(DateTime)
    material = Column(String)
    notes = Column(String)


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True)
    wafer_id = Column(Integer, ForeignKey("wafers.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    pixel_geometry = Column(String)
    width_nm = Column(Float)
    length_um = Column(Float)
    metadata_json = Column("metadata", JSON)

    wafer = relationship("Wafer")


class Cryostat(Base):
    __tablename__ = "cryostats"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    location = Column(String)
    notes = Column(String)


class Run(Base):
    __tablename__ = "runs"

    id = Column(Integer, primary_key=True)
    cryostat_id = Column(Integer, ForeignKey("cryostats.id"), nullable=False, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True, index=True)
    run_type = Column(Enum(RunType), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    ended_at = Column(DateTime)
    operator = Column(String)
    description = Column(String)
    # The measurement's own params: the sweep, the gate time, the choreography.
    config = Column(JSON)
    # How each instrument was configured when the run started, by name. The
    # config tree those values came from is edited between runs, so without
    # this nothing says which calibration a curve was taken at.
    instruments = Column(JSON)

    cryostat = relationship("Cryostat")
    device = relationship("Device")
    measurements = relationship(
        "Measurement", back_populates="run", cascade="all, delete-orphan"
    )


class Measurement(Base):
    __tablename__ = "measurements"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("runs.id"), nullable=False, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)

    counts = Column(Integer)
    int_time = Column(Float)
    delta_time = Column(Float)
    temperature = Column(Float)

    data_json = Column("data", JSON)
    metadata_json = Column("metadata", JSON)

    run = relationship("Run", back_populates="measurements")
    details = relationship(
        "MeasurementDetail",
        back_populates="measurement",
        cascade="all, delete-orphan",
    )


class MeasurementDetail(Base):
    __tablename__ = "measurement_details"

    id = Column(Integer, primary_key=True)
    measurement_id = Column(
        Integer, ForeignKey("measurements.id"), nullable=False, index=True
    )
    detail_type = Column(String, nullable=False)
    bin_index = Column(Integer)
    bin_value = Column(Float)
    value = Column(Float, nullable=False)

    measurement = relationship("Measurement", back_populates="details")


Index("idx_runs_cryostat_started", Run.cryostat_id, Run.started_at)
Index("idx_runs_type", Run.run_type)
Index("idx_measurements_timestamp", Measurement.timestamp)
