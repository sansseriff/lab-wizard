"""The Attenuator behavior and its two implementers.

Before this ABC existed, the Yokogawa AQ2212 and Ando AQ8201-31 attenuators
spelled the same four ideas five different ways, and no measurement could bind
to either. These tests pin the contract, the SCPI each driver emits for it, and
the two ways it reaches a measurement: the wizard's picker and a server proxy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_wizard.lib.client.proxies.attenuator import RemoteAttenuator
from lab_wizard.lib.client.proxies.registry import proxy_class_for
from lab_wizard.lib.instruments.andoAQ8201A.modules.attenuator31 import (
    Attenuator31,
    Attenuator31Params,
)
from lab_wizard.lib.instruments.general.attenuator import (
    Attenuator,
    StandInAttenuator,
)
from lab_wizard.lib.instruments.general.behavior import behavior_name_for
from lab_wizard.lib.instruments.general.state_effects import Arg, collect_state_methods
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.attenuator import (
    YokoAttenuator,
    YokoAttenuatorParams,
)
from lab_wizard.lib.utilities.config_io import instrument_hash


class RecordingDep:
    """Stands in for a slot dep: records writes, answers queries from a table."""

    def __init__(self, slot: int = 3, *, offline: bool = False, replies=None):
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


def _yoko(**dep_kwargs) -> tuple[YokoAttenuator, RecordingDep]:
    dep = RecordingDep(**dep_kwargs)
    return YokoAttenuator(dep, YokoAttenuatorParams(slot=dep.slot)), dep  # type: ignore[arg-type]


def _ando(**dep_kwargs) -> tuple[Attenuator31, RecordingDep]:
    dep = RecordingDep(**dep_kwargs)
    return Attenuator31(dep, Attenuator31Params(slot=dep.slot)), dep  # type: ignore[arg-type]


# --------------------------- the contract ---------------------------


@pytest.mark.parametrize("cls", [YokoAttenuator, Attenuator31, StandInAttenuator])
def test_every_implementer_satisfies_the_abc(cls):
    assert issubclass(cls, Attenuator)
    assert cls.__abstractmethods__ == frozenset()


@pytest.mark.parametrize("cls", [YokoAttenuator, Attenuator31])
def test_drivers_classify_as_attenuators_without_instantiation(cls):
    """What the server reports as ``behavior_abc``, and what the picker matches on."""
    assert behavior_name_for(cls, is_class=True) == "Attenuator"


def test_safety_state_declarations_reach_every_implementer():
    """The permission gate reads these; a driver must not have to repeat them."""
    for cls in (YokoAttenuator, Attenuator31, RemoteAttenuator):
        declared = collect_state_methods(cls)
        assert declared["open_shutter"] == ("shutter", "open")
        assert declared["close_shutter"] == ("shutter", "closed")
        key, spec = declared["set_attenuation"]
        assert key == "attenuation_db" and isinstance(spec, Arg) and spec.index == 0


# --------------------------- SCPI per driver ---------------------------


def test_yokogawa_speaks_its_own_dialect():
    att, dep = _yoko(slot=2, replies={"INP2:ATT?": "12.5"})
    assert att.set_attenuation(12.5) is True
    assert att.open_shutter() is True
    assert att.close_shutter() is True
    assert dep.writes == ["INP2:ATT 12.5", "OUTP2:STAT 1", "OUTP2:STAT 0"]
    assert att.get_attenuation() == 12.5


def test_ando_speaks_its_own_dialect_and_reads_attenuation_from_status():
    """The AQ8201-31 has no attenuation-only query; it comes back beside wavelength.

    The reply below is shaped to the driver's existing ``get_status`` parser
    (wavelength at characters 6-10 of the first token), not to a captured
    instrument reply.
    """
    att, dep = _ando(replies={"AD?": "STATUS1550 7.25"})
    assert att.set_attenuation(7.25) is True
    assert att.open_shutter() is True
    assert att.close_shutter() is True
    assert dep.writes == ["AAV 7.25", "ASHTR 0", "ASHTR 1"]
    assert att.get_attenuation() == 7.25
    assert dep.queries == ["AD?"]


@pytest.mark.parametrize("make", [_yoko, _ando])
def test_offline_getter_reports_the_last_commanded_value(make):
    """An offline dep answers "" to everything; the getter must not try to parse it."""
    att, _dep = make(offline=True)
    att.set_attenuation(33.0)
    assert att.get_attenuation() == 33.0


@pytest.mark.parametrize("make", [_yoko, _ando])
def test_max_attenuation_is_a_param_not_a_hardcoded_number(make):
    att, _dep = make()
    att.params.max_attenuation = 45.0
    assert att.get_max_attenuation() == 45.0


# --------------------------- safe state ---------------------------


@pytest.mark.parametrize(
    ("make", "expected"),
    [
        (_yoko, ["OUTP3:STAT 0", "INP3:ATT 60.0"]),
        (_ando, ["ASHTR 1", "AAV 60.0"]),
    ],
)
def test_safe_state_closes_the_shutter_before_maximising_attenuation(make, expected):
    att, dep = make()
    assert att.enter_safe_state() is True
    assert dep.writes == expected


def test_safe_state_still_maximises_attenuation_if_the_shutter_reports_failure():
    class StuckShutter(StandInAttenuator):
        def close_shutter(self) -> bool:
            return False

    att = StuckShutter()
    att.attenuation_db = 3.0
    assert att.enter_safe_state() is False
    assert att.attenuation_db == att.max_attenuation_db


# --------------------------- through a server ---------------------------


class RecordingSession:
    def __init__(self):
        self.calls: list[tuple[str, str, list, dict]] = []

    def call_inst(self, path, method, args, kwargs):
        self.calls.append((path, method, args, kwargs))
        return 60.0 if method == "get_max_attenuation" else True


def test_attenuator_resolves_to_a_typed_proxy():
    assert proxy_class_for("Attenuator") is RemoteAttenuator
    assert RemoteAttenuator.__abstractmethods__ == frozenset()


def test_remote_safe_state_decomposes_into_calls_the_gate_can_record():
    """Forwarded as one opaque RPC, the gate would never learn the shutter closed."""
    session = RecordingSession()
    proxy = RemoteAttenuator(session, "inst://abc/def", "bench_attenuator")  # type: ignore[arg-type]
    assert isinstance(proxy, Attenuator)

    assert proxy.enter_safe_state() is True
    assert [method for _path, method, _a, _k in session.calls] == [
        "close_shutter",
        "get_max_attenuation",
        "set_attenuation",
    ]
    assert session.calls[-1][2] == [60.0]


# --------------------------- selection and config ---------------------------


def test_an_attenuator_requirement_matches_both_drivers():
    """The concrete gap this closes: neither attenuator could be picked for anything."""
    from lab_wizard.wizard.backend.get_measurements import discover_matching_instruments
    from lab_wizard.wizard.backend.models import Env

    import lab_wizard.lib as lib_pkg

    env = Env(base_dir=Path(lib_pkg.__file__).parent)
    matched = {m.class_name for m in discover_matching_instruments(env, Attenuator)}
    assert {"YokoAttenuator", "Attenuator31"} <= matched


def test_rename_leaves_config_identity_untouched():
    """Configs written against ``Attenuator`` must load against ``YokoAttenuator``.

    Hashes come from the ``type`` literal and the addressing value, never the
    class name, and the literal did not change.
    """
    params = YokoAttenuatorParams.model_validate(
        {"type": "yoko_attenuator", "slot": 4, "attribute_name": "bench", "wavelength_nm": 1310.0}
    )
    assert params.resource_class() is YokoAttenuator
    assert params.max_attenuation == 60.0  # absent from old configs: default applies
    assert instrument_hash("yoko_attenuator", params.key_fields()) == instrument_hash(
        "yoko_attenuator", "4"
    )
