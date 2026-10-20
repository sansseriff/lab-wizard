"""The Laser behavior and its implementer (procedure plan 6.5).

The third behavior written to the shape ``VSource`` and ``Attenuator`` set, and
the one that keeps that shape honest: the AQ2212 laser module existed with a
``set_output(bool)`` and no way for any measurement to bind to it. These tests
pin the contract, the SCPI the driver emits for it, the safe state, and the two
ways it reaches a measurement — the wizard's picker and a server proxy.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from lab_wizard.lib.client.proxies.laser import RemoteLaser
from lab_wizard.lib.client.proxies.registry import proxy_class_for
from lab_wizard.lib.client.session import Session
from lab_wizard.lib.instruments.general.behavior import behavior_name_for
from lab_wizard.lib.instruments.general.laser import Laser, StandInLaser
from lab_wizard.lib.instruments.general.state_effects import (
    Arg,
    collect_query_methods,
    collect_state_methods,
)
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.laser import YokoLaser, YokoLaserParams
from lab_wizard.lib.utilities.config_io import instrument_hash


class RecordingDep:
    """Stands in for a slot dep: records writes, answers queries from a table."""

    def __init__(self, slot: int = 2, *, offline: bool = False, replies=None):
        self.slot = slot
        self.offline = offline
        self.writes: list[str] = []
        self.queries: list[str] = []
        self._replies = dict(replies or {})

    def write(self, cmd: str) -> None:
        self.writes.append(cmd)

    def query(self, cmd: str) -> str:
        self.queries.append(cmd)
        return "" if self.offline else self._replies.get(cmd, "")


def _yoko(**dep_kwargs) -> tuple[YokoLaser, RecordingDep]:
    dep = RecordingDep(**dep_kwargs)
    return YokoLaser(cast(YokoAQ2212SlotDep, dep), YokoLaserParams(slot=str(dep.slot))), dep


# --------------------------- the contract ---------------------------


@pytest.mark.parametrize("cls", [YokoLaser, StandInLaser])
def test_every_implementer_satisfies_the_abc(cls):
    assert issubclass(cls, Laser)
    assert cls.__abstractmethods__ == frozenset()


def test_the_driver_classifies_as_a_laser_without_instantiation():
    """The picker asks the class, never an instance — nothing opens a port."""
    assert behavior_name_for(YokoLaser, is_class=True) == "Laser"


def test_safety_state_declarations_reach_every_implementer():
    """A permission rule keys on these, so they must survive the MRO."""
    for cls in (YokoLaser, StandInLaser, RemoteLaser):
        effects = collect_state_methods(cls)
        assert effects["turn_on"] == ("output", "on")
        assert effects["turn_off"] == ("output", "off")
        key, spec = effects["set_power_dbm"]
        assert key == "power_dbm" and isinstance(spec, Arg) and spec.index == 0


def test_reads_are_declared_so_a_claim_does_not_block_watching_a_run():
    """Queries are fail-closed on the server: unlisted means refused."""
    queries = collect_query_methods(YokoLaser)
    assert {"get_power_dbm", "get_wavelength_nm", "is_output_on"} <= queries
    assert "set_power_dbm" not in queries


# --------------------------- the driver's dialect ---------------------------


def test_yokogawa_speaks_its_own_dialect():
    laser, dep = _yoko(replies={"SOUR2:POW:AMPL?": "-3.5", "SOUR2:POW:STAT?": "1"})

    assert laser.turn_on() is True
    assert laser.set_power_dbm(-3.5) is True
    assert dep.writes == ["SOUR2:POW:STAT ON", "SOUR2:POW:AMPL -3.5"]
    assert laser.get_power_dbm() == pytest.approx(-3.5)
    assert laser.is_output_on() is True


def test_wavelength_is_read_in_nanometres_though_the_module_speaks_hertz():
    """The conversion lives in the driver: nothing above it should know about THz."""
    laser, dep = _yoko(replies={"SOUR2:FREQ?": "193414489032258.06"})
    assert laser.get_wavelength_nm() == pytest.approx(1550.0, abs=0.01)

    assert laser.set_wavelength_nm(1310.0) is True
    assert dep.writes[-1] == "SOUR2:FREQ 228849204580152.7"


def test_offline_getters_report_the_last_commanded_value():
    """Offline the dep answers "", which float() cannot parse."""
    laser, _dep = _yoko(offline=True)
    laser.turn_on()
    laser.set_power_dbm(-10.0)
    assert laser.is_output_on() is True
    assert laser.get_power_dbm() == pytest.approx(-10.0)
    assert laser.get_wavelength_nm() == pytest.approx(1550.0)


def test_the_baseline_tunes_to_the_configured_wavelength():
    """Which colour the bench emits is config, re-applied at the start of a run."""
    dep = RecordingDep(slot=2)
    laser = YokoLaser(cast(YokoAQ2212SlotDep, dep), YokoLaserParams(slot="2", wavelength_nm=1310.0))
    assert laser.apply_baseline() is True
    assert dep.writes == ["SOUR2:FREQ 228849204580152.7"]


# --------------------------- safe state ---------------------------


def test_safe_state_stops_the_light():
    laser, dep = _yoko()
    laser.turn_on()
    assert laser.enter_safe_state() is True
    assert dep.writes[-1] == "SOUR2:POW:STAT OFF"


# --------------------------- through a server ---------------------------


class RecordingSession:
    def __init__(self):
        self.calls: list[tuple[str, str, list, dict]] = []

    def call_inst(self, path, method, args, kwargs):
        self.calls.append((path, method, args, kwargs))
        return True


def test_laser_resolves_to_a_typed_proxy():
    assert proxy_class_for("Laser") is RemoteLaser
    assert RemoteLaser.__abstractmethods__ == frozenset()


def test_remote_safe_state_is_a_call_the_gate_can_record():
    """Forwarded as one opaque RPC, the gate would never learn the laser went off."""
    session = RecordingSession()
    proxy = RemoteLaser(cast(Session, session), "inst://abc/def", "bench_laser")
    assert isinstance(proxy, Laser)

    assert proxy.enter_safe_state() is True
    assert [method for _path, method, _a, _k in session.calls] == ["turn_off"]


# --------------------------- selection and config ---------------------------


def test_a_laser_requirement_matches_the_driver():
    """The gap this closes: no measurement could be bound to a laser at all."""
    from lab_wizard.wizard.backend.get_measurements import discover_matching_instruments
    from lab_wizard.wizard.backend.models import Env

    import lab_wizard.lib as lib_pkg

    env = Env(base_dir=Path(lib_pkg.__file__).parent)
    matched = {m.class_name for m in discover_matching_instruments(env, Laser)}
    assert "YokoLaser" in matched


def test_rename_leaves_config_identity_untouched():
    """Configs written against ``Laser`` must load against ``YokoLaser``.

    Hashes come from the ``type`` literal and the addressing value, never the
    class name, and the literal did not change.
    """
    params = YokoLaserParams.model_validate(
        {"type": "yoko_laser", "slot": 4, "attribute_name": "bench_laser"}
    )
    assert params.type == "yoko_laser"
    assert instrument_hash("yoko_laser", "4") == instrument_hash(params.type, str(params.slot))


# --------------------------- in a procedure ---------------------------


def test_a_procedure_can_drive_a_laser_and_guard_it():
    """The steps a laser needs, and safe_guard accepting one.

    Without these the ABC would be visible to the picker but unusable in a
    composed procedure — the gap this phase exists to close.
    """
    from lab_procedure import ProcedureRunner, Status

    from lab_procedure import step_catalog
    from lab_wizard.lib.procedures.definition import ProcedureDefinition

    catalog = step_catalog()
    assert catalog["laser_on"]["fields"]["laser"]["requires"] == ["Laser"]
    assert "Laser" in catalog["safe_guard"]["fields"]["instrument"]["requires"]

    definition = ProcedureDefinition.model_validate(
        {
            "name": "warm_up",
            "roles": {"laser": {"behavior": "Laser"}},
            "params": {"power_dbm": {"type": "float", "default": -3.0, "unit": "dBm"}},
            "body": {
                "type": "safe_guard",
                "instrument": {"role": "laser"},
                "body": {
                    "type": "sequence",
                    "children": [
                        {"type": "set_laser_power", "laser": {"role": "laser"}, "power_dbm": {"param": "power_dbm"}},
                        {"type": "laser_on", "laser": {"role": "laser"}},
                    ],
                },
            },
        }
    )
    definition.check()  # raises if it could not be generated

    class Resources:
        params = definition.params_model()()
        laser = StandInLaser()

    resources = Resources()
    module: dict = {}
    exec(  # noqa: S102 - the generated module is the thing under test
        compile(
            __import__("lab_wizard.lib.procedures.codegen", fromlist=["x"]).measurement_module_source(definition),
            "<warm_up>",
            "exec",
        ),
        module,
    )
    status = ProcedureRunner(instruments=resources).run(module["build_warm_up_procedure"](resources))

    assert status is Status.SUCCESS
    assert resources.laser.get_power_dbm() == pytest.approx(-3.0)
    # The guard put it back in its safe state on the way out.
    assert resources.laser.is_output_on() is False
