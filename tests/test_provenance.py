"""What a run records about the instruments it drove (procedure plan 5.6).

A project names its instruments and reads their settings from a config tree it
does not own. That tree is edited between runs, so without a snapshot taken at
run start nothing says which calibration a curve was taken at. These tests pin
the snapshot, the read that makes it possible through a server, and the column
it lands in — including for a database written before that column existed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast


from lab_wizard.lib.client.proxies.attenuator import RemoteAttenuator
from lab_wizard.lib.client.session import Session
from lab_wizard.lib.instruments.fake_rack.fake_attenuator import FakeAttenuator, FakeAttenuatorParams
from lab_wizard.lib.instruments.general.attenuator import StandInAttenuator
from lab_wizard.lib.savers.database_saver import DatabaseSaver
from lab_wizard.lib.task_adapters.provenance import baseline_snapshot, instrument_params


class RecordingSession:
    """Answers the params read the way a server does."""

    def __init__(self, params: dict[str, Any] | None):
        self.params = params
        self.calls: list[tuple[str, dict]] = []

    def call(self, method: str, payload: dict | None = None):
        self.calls.append((method, payload or {}))
        return self.params


def _local_attenuator(**kwargs) -> FakeAttenuator:
    params = FakeAttenuatorParams(port="sim://prov-att", attribute_name="bench_att", **kwargs)
    return params.create_inst()


# --------------------------- the snapshot ---------------------------


def test_a_local_instrument_reports_the_params_it_was_built_from():
    attenuator = _local_attenuator(wavelength_nm=1310.0)
    params = instrument_params(attenuator)

    assert params is not None
    assert params["wavelength_nm"] == 1310.0
    assert params["type"] == "fake_attenuator"
    # Each instrument answers for itself; a rack does not carry its modules.
    assert "children" not in params


def test_a_routed_instrument_is_read_through_its_server():
    session = RecordingSession({"type": "yoko_attenuator", "wavelength_nm": 1550.0})
    proxy = RemoteAttenuator(cast(Session, session), "inst://rack/att", "bench_att")

    assert instrument_params(proxy) == {"type": "yoko_attenuator", "wavelength_nm": 1550.0}
    assert session.calls == [("params_get", {"path": "inst://rack/att"})]


def test_a_proxy_reports_its_name_as_a_name_not_a_remote_call():
    """``__getattr__`` makes any unknown attribute a remote *call*.

    Reading a proxy's name reflectively used to return a function, which then
    became a dict key in the snapshot and failed at the database.
    """
    proxy = RemoteAttenuator(cast(Session, RecordingSession(None)), "inst://rack/att", "bench_att")
    assert proxy.attribute_name == "bench_att"


def test_an_instrument_that_cannot_answer_is_left_out_rather_than_failing_the_run():
    assert instrument_params(StandInAttenuator()) is None

    class Unreachable(RecordingSession):
        def call(self, method: str, payload: dict | None = None):
            raise RuntimeError("server went away")

    proxy = RemoteAttenuator(cast(Session, Unreachable(None)), "inst://rack/att", "bench_att")
    assert instrument_params(proxy) is None


def test_the_snapshot_is_keyed_by_the_name_the_project_uses():
    @dataclass
    class Resources:
        attenuator: Any
        savers: list = field(default_factory=list)
        params: Any = None

    resources = Resources(attenuator=_local_attenuator())
    snapshot = baseline_snapshot(resources)

    assert list(snapshot) == ["bench_att"]
    assert snapshot["bench_att"]["port"] == "sim://prov-att"
    json.dumps(snapshot)  # it has to survive the trip to the database


# --------------------------- into the database ---------------------------


def test_the_run_row_carries_the_snapshot_beside_the_measurement_params(tmp_path: Path):
    saver = DatabaseSaver(str(tmp_path / "m.db"))
    saver.start_run(
        run_type="mcr_curve",
        config={"attenuation": {"settle_s": 0.2}},
        instruments={"bench_att": {"wavelength_nm": 1310.0}},
    )
    saver.end_run()
    saver.close()

    row = sqlite3.connect(tmp_path / "m.db").execute("select config, instruments from runs").fetchone()
    assert json.loads(row[0]) == {"attenuation": {"settle_s": 0.2}}
    assert json.loads(row[1]) == {"bench_att": {"wavelength_nm": 1310.0}}


def test_a_database_written_before_the_column_existed_still_opens(tmp_path: Path):
    """There are no migrations here, and last month's database must still work."""
    db_path = tmp_path / "old.db"
    saver = DatabaseSaver(str(db_path))
    saver.start_run(run_type="iv_curve", config={"old": True})
    saver.end_run()
    saver.close()

    with sqlite3.connect(db_path) as db:
        db.execute("alter table runs drop column instruments")  # an older schema
        assert "instruments" not in {c[1] for c in db.execute("pragma table_info(runs)")}

    reopened = DatabaseSaver(str(db_path))
    reopened.start_run(run_type="mcr_curve", instruments={"bench_att": {"wavelength_nm": 1550.0}})
    reopened.end_run()
    reopened.close()

    rows = sqlite3.connect(db_path).execute("select run_type, instruments from runs order by id").fetchall()
    assert rows[0] == ("IV_CURVE", None)  # the old run keeps its data
    assert json.loads(rows[1][1]) == {"bench_att": {"wavelength_nm": 1550.0}}
