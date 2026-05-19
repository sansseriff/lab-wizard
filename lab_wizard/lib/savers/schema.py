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
    config = Column(JSON)

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
