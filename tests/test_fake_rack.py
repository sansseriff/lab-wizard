"""The simulated SNSPD rack: physics, wire protocol, and driver stack.

These tests exercise the fake rack from three angles, because it exists to be
trusted by other tests:

* the detector model on its own — switching, latching, retrapping;
* the bytes on the wire — that the *real* SIM900 drivers, unmodified, produce
  commands the simulated rack understands;
* the assembled stack — that a config tree of ``fakegpib``/``fake900``/
  ``fake928``/``fake970`` yields objects satisfying ``VSource``/``VSense`` and
  traces a sensible IV curve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900, Fake900Params
from lab_wizard.lib.instruments.fake_rack.fakegpib import (
    DEFAULT_FAKE_PORT,
    FakeGpib,
    FakeGpibParams,
)
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928, Fake928Params
from lab_wizard.lib.instruments.fake_rack.modules.fake970 import Fake970, Fake970Params
from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams
from lab_wizard.lib.instruments.general.discovery import NoParams
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    load_instruments,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities import params_discovery

GPIB_ADDRESS = "5"
SOURCE_SLOT = "1"
METER_SLOT = "2"
PORT = "sim://test-rack"

# Config keys are the same content hashes the wizard writes, so a tree built
# here reloads from YAML unchanged.
GPIB_KEY = instrument_hash("fakegpib", PORT)
MAINFRAME_KEY = instrument_hash("fake900", GPIB_ADDRESS)
SOURCE_KEY = instrument_hash("fake928", SOURCE_SLOT)
METER_KEY = instrument_hash("fake970", METER_SLOT)

# Defaults: 0.3 µA switching current behind the model's fixed 100 kΩ bias
# resistor, i.e. the detector switches at 0.03 V applied.
SWITCHING_BIAS_V = 0.03


def rack_params(**device_overrides: float) -> FakeGpibParams:
    """A one-detector rack: source in slot 1, voltmeter in slot 2."""
    return FakeGpibParams(
        port=PORT,
        children={
            MAINFRAME_KEY: Fake900Params(
                gpib_address=GPIB_ADDRESS,
                device=SnspdModelParams(**device_overrides),
                children={
                    SOURCE_KEY: Fake928Params(slot=SOURCE_SLOT),
                    METER_KEY: Fake970Params(slot=METER_SLOT, device_channel=0),
                },
            )
        },
    )


def build_rack(**device_overrides: float) -> tuple[FakeGpib, Fake928, Fake970]:
    controller = rack_params(**device_overrides).create_inst()
    mainframe = controller.make_child(MAINFRAME_KEY)
    source = mainframe.make_child(SOURCE_KEY)
    meter = mainframe.make_child(METER_KEY)
    assert isinstance(source, Fake928)
    assert isinstance(meter, Fake970)
    return controller, source, meter


# ---------------------------------------------------------------------------
# The detector model
# ---------------------------------------------------------------------------


def test_model_reads_zero_below_the_switching_current() -> None:
    model = SnspdModel()
    model.set_output_enabled(True)
    for bias in (0.0, 0.005, 0.01, 0.02, 0.029):
        model.set_bias_voltage(bias)
        assert model.device_voltage() == 0.0
        assert model.bias_current() == pytest.approx(bias / 100_000.0)
        assert not model.is_normal


def test_model_switches_at_the_critical_current() -> None:
    model = SnspdModel()
    model.set_output_enabled(True)

    model.set_bias_voltage(SWITCHING_BIAS_V - 0.001)
    assert model.device_voltage() == 0.0

    model.set_bias_voltage(SWITCHING_BIAS_V)
    switched = model.device_voltage()
    assert model.is_normal
    # V = I·R_n on the series divider: 0.03 V / 150 kΩ × 50 kΩ.
    assert switched == pytest.approx(0.03 / 150_000.0 * 50_000.0)
    assert switched > 0


def test_model_normal_branch_rises_monotonically_with_bias() -> None:
    model = SnspdModel()
    model.set_output_enabled(True)
    readings = []
    for bias in (0.03, 0.04, 0.06, 0.08, 0.1, 0.14):
        model.set_bias_voltage(bias)
        readings.append(model.device_voltage())
    assert all(b > a for a, b in zip(readings, readings[1:]))


def test_model_is_hysteretic_once_latched() -> None:
    """Coming back down does not retrace: the hotspot stays until I < I_r."""
    model = SnspdModel()
    model.set_output_enabled(True)

    model.set_bias_voltage(0.02)
    assert model.device_voltage() == 0.0  # superconducting on the way up

    model.set_bias_voltage(0.05)
    assert model.device_voltage() > 0.0  # switched

    model.set_bias_voltage(0.03)
    assert model.device_voltage() > 0.0  # still latched at the same bias

    # 0.02 V puts 0.133 µA through the latched branch, below the 0.15 µA
    # retrapping current, so the detector re-cools.
    model.set_bias_voltage(0.02)
    assert model.device_voltage() == 0.0
    assert not model.is_normal


def test_model_with_output_off_sees_no_bias() -> None:
    model = SnspdModel()
    model.set_bias_voltage(1.0)
    assert model.device_voltage() == 0.0
    assert not model.is_normal
    model.set_output_enabled(True)
    assert model.device_voltage() > 0.0


def test_model_noise_is_seeded_and_reproducible() -> None:
    params = SnspdModelParams(noise_volts=1e-4, seed=7)
    first = SnspdModel(params)
    second = SnspdModel(params)
    first.set_output_enabled(True)
    second.set_output_enabled(True)
    first.set_bias_voltage(0.01)
    second.set_bias_voltage(0.01)

    a = [first.device_voltage() for _ in range(5)]
    b = [second.device_voltage() for _ in range(5)]
    assert a == b
    assert any(v != 0.0 for v in a), "noise should perturb the zero reading"
    assert all(abs(v) < 1e-3 for v in a)


# ---------------------------------------------------------------------------
# The wire protocol
# ---------------------------------------------------------------------------


def test_drivers_speak_the_real_sim900_wire_protocol() -> None:
    """The commands on the bus are the ones a real rack would receive.

    This is the assertion a mocked instrument object cannot make, and the
    reason the substitution happens at the serial port: the framing, the
    addressing, and the SCPI text are all produced by production code.
    """
    controller, source, meter = build_rack()

    source.turn_on()
    source.set_voltage(0.25)
    meter.channels[0].get_voltage()

    sent = controller.bus.commands_for(int(GPIB_ADDRESS))
    assert sent[:6] == [
        'CONN 1, "esc"',
        "OPON",
        "esc",
        'CONN 1, "esc"',
        "VOLT 0.250",
        "esc",
    ]
    # Sim970Channel reads twice per measurement (settle, then read) and
    # addresses hardware channels 1-based.
    assert sent[6:] == [
        'CONN 2, "esc"',
        "VOLT? 1",
        "esc",
        'CONN 2, "esc"',
        "VOLT? 1",
        "esc",
    ]
    assert controller.bus.history[0][0] == int(GPIB_ADDRESS)


def test_unknown_command_gets_silence_not_a_plausible_number() -> None:
    """A malformed command must fail loudly, not read back something parseable."""
    controller, _, _ = build_rack()
    mainframe = controller.children[MAINFRAME_KEY]
    slot = mainframe.dep.slot(int(SOURCE_SLOT))

    assert slot.query("NONSENSE?") == b""
    assert slot.query("VOLT?") == b"0.000\r\n"


def test_empty_slot_and_unused_address_never_answer() -> None:
    controller, _, _ = build_rack()
    mainframe = controller.children[MAINFRAME_KEY]

    assert mainframe.dep.slot(7).query("VOLT?") == b""
    assert controller.dep.query_instrument(11, "*IDN?") == b""


def test_mainframe_and_modules_identify_themselves_as_simulated() -> None:
    controller, _, _ = build_rack()
    idn = controller.dep.query_instrument(int(GPIB_ADDRESS), "*IDN?")
    assert b"FAKE900" in idn
    assert b"Lab_Wizard_Simulation" in idn

    mainframe = controller.children[MAINFRAME_KEY]
    assert b"FAKE928" in mainframe.dep.slot(int(SOURCE_SLOT)).query("*IDN?")
    assert b"FAKE970" in mainframe.dep.slot(int(METER_SLOT)).query("*IDN?")


def test_unwired_voltmeter_channels_float_around_zero() -> None:
    _, source, meter = build_rack(noise_volts=1e-5)
    source.turn_on()
    source.set_voltage(1.0)

    assert meter.channels[0].get_voltage() > 1e-3  # wired to the detector
    for index in (1, 2, 3):
        assert abs(meter.channels[index].get_voltage()) < 1e-3


def test_voltmeter_rejects_out_of_range_channels() -> None:
    controller, _, _ = build_rack()
    mainframe = controller.children[MAINFRAME_KEY]
    assert mainframe.dep.slot(int(METER_SLOT)).query("VOLT? 9") == b""


# ---------------------------------------------------------------------------
# The assembled stack
# ---------------------------------------------------------------------------


def test_rack_satisfies_the_behaviors_measurements_bind_to() -> None:
    _, source, meter = build_rack()
    assert isinstance(source, VSource)
    assert isinstance(meter.channels[0], VSense)


def test_iv_sweep_through_the_drivers_traces_the_detector() -> None:
    _, source, meter = build_rack()
    sense = meter.channels[0]

    source.turn_on()
    curve = []
    for bias in [i * 0.005 for i in range(29)]:  # 0.0 .. 0.14 V
        source.set_voltage(bias)
        curve.append((bias, sense.get_voltage()))

    below = [v for bias, v in curve if bias < SWITCHING_BIAS_V]
    above = [v for bias, v in curve if bias >= SWITCHING_BIAS_V]
    assert set(below) == {0.0}
    assert all(v > 0 for v in above)
    assert all(b > a for a, b in zip(above, above[1:]))

    source.turn_off()
    assert sense.get_voltage() == 0.0


def test_source_voltage_is_quantized_the_way_the_hardware_quantizes_it() -> None:
    """The SIM928 driver formats to millivolts; the rack sees only that."""
    controller, source, meter = build_rack()
    source.turn_on()
    source.set_voltage(0.03049)

    assert "VOLT 0.030" in controller.bus.commands_for(int(GPIB_ADDRESS))
    assert meter.channels[0].get_voltage() == pytest.approx(
        0.03 / 150_000.0 * 50_000.0
    )


# ---------------------------------------------------------------------------
# Discovery and config round-trip
# ---------------------------------------------------------------------------


def test_scan_usb_offers_a_simulated_controller() -> None:
    result = FakeGpibParams._scan_usb(NoParams())
    assert [f.port for f in result.found] == [DEFAULT_FAKE_PORT]


def test_missing_type_refreshes_a_stale_live_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server started before the fake rack was added can load it on demand."""
    stale_map = {
        type_name: info
        for type_name, info in params_discovery.get_type_to_module_map().items()
        if type_name != "fakegpib"
    }
    stale_loaded = dict(params_discovery._loaded_params["instrument"])
    stale_loaded.pop("fakegpib", None)
    monkeypatch.setitem(params_discovery._type_to_module, "instrument", stale_map)
    monkeypatch.setitem(params_discovery._loaded_params, "instrument", stale_loaded)

    assert params_discovery.load_params_class("fakegpib") is FakeGpibParams
    assert "fakegpib" in params_discovery.get_type_to_module_map()


def test_fake970_metadata_declares_voltage_sense_channels() -> None:
    metadata = params_discovery.get_instrument_metadata()

    assert metadata["sim970"]["channel_behavior_abc"] == "VSense"
    assert metadata["fake970"]["channel_behavior_abc"] == "VSense"


def test_scan_gpib_finds_the_simulated_mainframe_over_the_wire() -> None:
    """Discovery uses the same ``*IDN?`` walk it uses on real hardware."""
    controller = rack_params().create_inst()
    result = Fake900Params._scan_gpib(NoParams(), controller)
    assert [c.key_fields["gpib_address"] for c in result.found] == [GPIB_ADDRESS]
    assert "FAKE900" in (result.found[0].idn or "")


def test_server_discovery_releases_a_rack_opened_only_for_the_scan(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    save_instruments_to_config({GPIB_KEY: rack_params()}, config_dir)
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    server = WireServer(bind="inproc://unused", registry=registry)

    result = server.discover(
        type="fake900",
        action="scan_gpib",
        parent_chain=[{"type": "fakegpib", "key": GPIB_KEY}],
    )

    assert result["found"][0]["key_fields"]["gpib_address"] == GPIB_ADDRESS
    assert registry.held_roots() == set()


def test_server_discovery_preserves_a_rack_that_was_already_open(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    save_instruments_to_config({GPIB_KEY: rack_params()}, config_dir)
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    server = WireServer(bind="inproc://unused", registry=registry)
    root_path = f"inst://{GPIB_KEY}"
    existing = registry.resolve(root_path)

    server.discover(
        type="fake900",
        action="scan_gpib",
        parent_chain=[{"type": "fakegpib", "key": GPIB_KEY}],
    )

    assert registry.resolve(root_path) is existing
    assert registry.held_roots() == {root_path}


def test_config_tree_round_trips_and_still_sweeps(tmp_path: Path) -> None:
    """A saved and reloaded config produces a working simulated rack."""
    config_dir = tmp_path / "config"
    params = rack_params()
    instruments = {GPIB_KEY: params}
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)

    reloaded = load_instruments(config_dir)
    assert list(reloaded) == [GPIB_KEY]

    controller = FakeGpib.from_params(reloaded[GPIB_KEY])
    mainframe = Fake900.from_config(controller, key=MAINFRAME_KEY)
    source = Fake928.from_config(mainframe, key=SOURCE_KEY)
    meter = Fake970.from_config(mainframe, key=METER_KEY)

    source.turn_on()
    source.set_voltage(1.0)
    assert meter.channels[0].get_voltage() > 0.0


def test_saved_config_names_every_channel(tmp_path: Path) -> None:
    """The fake rack participates in attribute naming like any other instrument."""
    config_dir = tmp_path / "config"
    params = rack_params()
    instruments = {GPIB_KEY: params}
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)

    reloaded = load_instruments(config_dir)
    mainframe = next(iter(reloaded.values())).children
    meter = next(p for p in mainframe.values() for p in p.children.values() if p.type == "fake970")
    assert sorted(meter.channels) == [0, 1, 2, 3]
    assert all(ch.attribute_name for ch in meter.channels.values())
