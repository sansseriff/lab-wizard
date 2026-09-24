from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session

from lab_procedure import MessageBus, Point, RunEnded, RunStarted
from lab_wizard.lib.savers.database_saver import DatabaseSaver
from lab_wizard.lib.savers.saver import StandInSaver
from lab_wizard.lib.savers.schema import Measurement
from lab_wizard.lib.task_adapters import SaverSink


def test_saver_sink_writes_a_point_to_the_stand_in_saver() -> None:
    saver = StandInSaver()
    sink = SaverSink([saver])
    bus = MessageBus()
    bus.subscribe((RunStarted, Point, RunEnded), sink.handle)

    bus.emit(RunStarted(run_type="iv_curve", device="device-a"))
    bus.emit(
        Point(
            seq=3,
            t=datetime(2026, 9, 23, tzinfo=UTC),
            values={"bias_voltage": 0.2, "counts": 5, "int_time": 1.0},
            steps=("sweep/count#1",),
        )
    )
    bus.emit(RunEnded(status="success"))

    assert saver.started
    assert saver.ended
    assert saver.measurements == [
        {
            "data": {"bias_voltage": 0.2, "counts": 5, "int_time": 1.0},
            "counts": 5,
            "int_time": 1.0,
            "delta_time": None,
            "temperature": None,
            "metadata": {"seq": 3, "steps": ["sweep/count#1"]},
            "details": [],
        }
    ]


def test_database_saver_persists_a_points_values_as_json(tmp_path: Path) -> None:
    db_path = tmp_path / "measurements.db"
    saver = DatabaseSaver(str(db_path))
    sink = SaverSink([saver])

    sink.handle(RunStarted(run_type="iv_curve", device="device-a"))
    sink.handle(
        Point(
            seq=0,
            t=datetime(2026, 9, 23, tzinfo=UTC),
            values={"bias_voltage": 0.3, "counts": 9, "int_time": 0.5, "delta_time": 0.51},
        )
    )
    sink.handle(RunEnded(status="success"))

    with Session(saver.engine) as session:
        measurement = session.query(Measurement).one()
        assert measurement.counts == 9
        assert measurement.data_json["bias_voltage"] == 0.3
        assert measurement.metadata_json == {"seq": 0, "steps": []}

    saver.close()
