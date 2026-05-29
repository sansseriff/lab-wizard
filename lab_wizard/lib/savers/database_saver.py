from __future__ import annotations

"""
SQLite-backed saver implementing the lab measurement schema from database_plan.md.

Each run produces one row in ``runs``; each integration produces one row in
``measurements`` plus optional rows in ``measurement_details``.  Cryostats and
devices are looked up by name and auto-created on first reference.
"""

from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TYPE_CHECKING

from pydantic import Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from lab_wizard.lib.savers.base import SaverParams
from lab_wizard.lib.savers.saver import GenericSaver
from lab_wizard.lib.savers.schema import (
    Base, Cryostat, Device, Measurement, MeasurementDetail, Run, RunType,
)

if TYPE_CHECKING:
    pass


class DatabaseSaverParams(SaverParams):
    """Params for the SQLite-backed lab measurement database saver."""

    type: Literal["database_saver"] = "database_saver"
    db_path: str = Field(
        default="measurements.db",
        description="Path to the SQLite file (relative to the project, or absolute).",
    )
    cryostat_name: str = Field(
        default="default",
        description="Cryostat to associate runs with — auto-created if missing.",
    )

    @property
    def inst(self) -> type["DatabaseSaver"]:
        return DatabaseSaver


def _coerce_run_type(value: str) -> RunType:
    try:
        return RunType(value)
    except ValueError:
        return RunType.OTHER


class DatabaseSaver(GenericSaver):
    """SQLite-backed saver. One row per integration; one ``runs`` row per run."""

    def __init__(self, db_path: str, cryostat_name: str = "default") -> None:
        self.db_path = db_path
        self.cryostat_name = cryostat_name
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self.engine)
        self.session: Session = Session(self.engine)
        self._cryostat_id: int | None = None
        self._current_run: Run | None = None

    @classmethod
    def from_params(cls, params: SaverParams) -> "DatabaseSaver":
        if not isinstance(params, DatabaseSaverParams):
            raise TypeError(
                f"DatabaseSaver.from_params expected DatabaseSaverParams, got {type(params).__name__}"
            )
        return cls(db_path=params.db_path, cryostat_name=params.cryostat_name)

    def _ensure_cryostat(self) -> int:
        if self._cryostat_id is not None:
            return self._cryostat_id
        cryostat = (
            self.session.query(Cryostat)
            .filter_by(name=self.cryostat_name)
            .one_or_none()
        )
        if cryostat is None:
            cryostat = Cryostat(name=self.cryostat_name)
            self.session.add(cryostat)
            self.session.commit()
        self._cryostat_id = cryostat.id
        return cryostat.id

    def _resolve_device_id(self, device_name: str | None) -> int | None:
        if not device_name:
            return None
        device = (
            self.session.query(Device).filter_by(name=device_name).one_or_none()
        )
        if device is None:
            device = Device(name=device_name)
            self.session.add(device)
            self.session.commit()
        return device.id

    def start_run(
        self,
        *,
        run_type: str,
        device: str | None = None,
        cryostat: str | None = None,
        operator: str | None = None,
        description: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        if cryostat:
            self.cryostat_name = cryostat
            self._cryostat_id = None
        cryostat_id = self._ensure_cryostat()
        device_id = self._resolve_device_id(device)

        self._current_run = Run(
            cryostat_id=cryostat_id,
            device_id=device_id,
            run_type=_coerce_run_type(run_type),
            operator=operator,
            description=description,
            config=config,
        )
        self.session.add(self._current_run)
        self.session.commit()

    def write_measurement(
        self,
        *,
        data: dict[str, Any] | None = None,
        counts: int | None = None,
        int_time: float | None = None,
        delta_time: float | None = None,
        temperature: float | None = None,
        metadata: dict[str, Any] | None = None,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        if self._current_run is None:
            raise RuntimeError(
                "DatabaseSaver.write_measurement called before start_run"
            )
        m = Measurement(
            run_id=self._current_run.id,
            counts=counts,
            int_time=int_time,
            delta_time=delta_time,
            temperature=temperature,
            data_json=data or {},
            metadata_json=metadata or {},
        )
        self.session.add(m)
        self.session.flush()

        if details:
            for d in details:
                self.session.add(
                    MeasurementDetail(
                        measurement_id=m.id,
                        detail_type=d.get("detail_type", "unknown"),
                        bin_index=d.get("bin_index"),
                        bin_value=d.get("bin_value"),
                        value=d["value"],
                    )
                )

        self.session.commit()

    def end_run(self) -> None:
        if self._current_run is None:
            return
        self._current_run.ended_at = datetime.utcnow()
        self.session.commit()
        self._current_run = None

    def close(self) -> None:
        self.session.close()
        self.engine.dispose()
