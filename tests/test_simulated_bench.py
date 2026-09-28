"""The simulated bench (lab_sim): physics, wire protocol, and driver stack.

lab_sim exists to be trusted by other tests, so it is checked from three
angles:

* the detector model on its own — switching, latching, retrapping;
* the bytes on the wire — that lab_wizard's *real* SIM900 drivers, unmodified,
  reach the simulated rack through an ordinary serial port and produce
  commands it understands;
* the assembled stack — that a config tree of ``prologix_gpib``/``sim900``/
  ``sim928``/``sim970`` pointed at the bench yields objects satisfying
  ``VSource``/``VSense`` and traces a sensible IV curve.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lab_sim import SnspdModel, SnspdParams
from lab_wizard.lib.instruments.general.discovery import NoParams
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIB
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970
from lab_wizard.lib.instruments.sim900.sim900 import Sim900, Sim900Params
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.lib.utilities.config_io import load_instruments, save_instruments_to_config
from lab_wizard.lib.utilities import resource_catalog

GPIB_ADDRESS = 5
SOURCE_SLOT = 1
METER_SLOT = 2

# Defaults: 0.3 µA switching current behind a 100 kΩ bias resistor, i.e. the
# detector switches at 0.03 V applied.
SWITCHING_BIAS_V = 0.03


def build_rack(rig) -> tuple[PrologixGPIB, Sim928, Sim970]:
    controller = rig.gpib_params().create_inst()
    mainframe = controller.make_child(rig.mainframe)
    source = mainframe.make_child(rig.source)
    meter = mainframe.make_child(rig.meter)
    assert isinstance(source, Sim928)
    assert isinstance(meter, Sim970)
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
    params = SnspdParams(noise_volts=1e-4, seed=7)
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


def test_drivers_speak_the_real_sim900_wire_protocol(rig) -> None:
    """The commands on the bus are the ones a real rack would receive.

    This is the assertion a mocked instrument object cannot make: the framing,
    the addressing, and the command text are all produced by production code,
    and they crossed a real serial port to get here.
    """
    _, source, meter = build_rack(rig)

    source.turn_on()
    source.set_voltage(0.25)
    meter.channels[0].get_voltage()

    sent = rig.bench.gpib.commands_for(GPIB_ADDRESS)
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


def test_the_controller_port_is_exclusive(rig) -> None:
    """Opened the way a USB controller is, so a second opener is refused."""
    first, _, _ = build_rack(rig)
    with pytest.raises(Exception, match="exclusively lock"):
        rig.gpib_params().create_inst()  # configuring the controller opens the port
    first.disconnect()
    second = rig.gpib_params().create_inst()
    assert b"SIM900" in second.dep.query_instrument(GPIB_ADDRESS, "*IDN?")


def test_unknown_command_gets_silence_not_a_plausible_number(rig) -> None:
    """A malformed command must fail loudly, not read back something parseable."""
    controller, _, _ = build_rack(rig)
    slot = controller.children[rig.mainframe].dep.slot(SOURCE_SLOT)

    assert slot.query("NONSENSE?") == b""
    assert slot.query("VOLT?") == b"0.000\r\n"


def test_empty_slot_and_unused_address_never_answer(rig) -> None:
    controller, _, _ = build_rack(rig)
    mainframe = controller.children[rig.mainframe]

    assert mainframe.dep.slot(7).query("VOLT?") == b""
    assert controller.dep.query_instrument(11, "*IDN?") == b""


def test_the_rack_identifies_as_simulated_hardware(rig) -> None:
    """Real model names, so the lab's scans find it; SIMULATED where the serial goes."""
    controller, _, _ = build_rack(rig)
    idn = controller.dep.query_instrument(GPIB_ADDRESS, "*IDN?")
    assert idn.startswith(b"Stanford_Research_Systems,SIM900,s/n SIMULATED")

    mainframe = controller.children[rig.mainframe]
    assert b"SIM928,s/n SIMULATED" in mainframe.dep.slot(SOURCE_SLOT).query("*IDN?")
    assert b"SIM970,s/n SIMULATED" in mainframe.dep.slot(METER_SLOT).query("*IDN?")


def test_unwired_voltmeter_channels_float_around_zero(make_rig) -> None:
    _, source, meter = build_rack(make_rig(noise_volts=1e-5))
    source.turn_on()
    source.set_voltage(1.0)

    assert meter.channels[0].get_voltage() > 1e-3  # wired to the detector
    for index in (1, 2, 3):
        assert abs(meter.channels[index].get_voltage()) < 1e-3


def test_voltmeter_rejects_out_of_range_channels(rig) -> None:
    controller, _, _ = build_rack(rig)
    mainframe = controller.children[rig.mainframe]
    assert mainframe.dep.slot(METER_SLOT).query("VOLT? 9") == b""


# ---------------------------------------------------------------------------
# The assembled stack
# ---------------------------------------------------------------------------


def test_rack_satisfies_the_behaviors_measurements_bind_to(rig) -> None:
    _, source, meter = build_rack(rig)
    assert isinstance(source, VSource)
    assert isinstance(meter.channels[0], VSense)


def test_iv_sweep_through_the_drivers_traces_the_detector(rig) -> None:
    _, source, meter = build_rack(rig)
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


def test_source_voltage_is_quantized_the_way_the_hardware_quantizes_it(rig) -> None:
    """The SIM928 driver formats to millivolts; the rack sees only that."""
    _, source, meter = build_rack(rig)
    source.turn_on()
    source.set_voltage(0.03049)

    # The reading comes back after the rack has handled everything sent before it.
    assert meter.channels[0].get_voltage() == pytest.approx(0.03 / 150_000.0 * 50_000.0)
    assert "VOLT 0.030" in rig.bench.gpib.commands_for(GPIB_ADDRESS)


def test_the_counter_and_attenuator_share_the_detector(rig) -> None:
    """Bias from the rack and light through the attenuator decide the counts."""
    _, source, _ = build_rack(rig)
    counter = rig.counter_params().create_inst().channels[0]
    attenuator = rig.yoko_params().create_inst().make_child(rig.attenuator)

    counter.set_threshold(-50.0)
    assert counter.count(0.1) == 0  # nothing biases the detector yet
    source.turn_on()
    source.set_voltage(0.026)
    bright = counter.count(0.1)
    assert bright > 10_000

    attenuator.set_attenuation(10.0)
    assert attenuator.get_attenuation() == 10.0
    assert rig.model.optical_transmission == pytest.approx(0.1)
    assert counter.count(0.1) < bright / 5

    attenuator.close_shutter()
    assert attenuator.is_shutter_open() is False
    assert counter.count(0.1) < 100  # dark counts only


def test_the_attenuator_clamps_and_quantizes_like_hardware(rig) -> None:
    attenuator = rig.yoko_params().create_inst().make_child(rig.attenuator)
    attenuator.set_attenuation(12.34567)
    assert attenuator.get_attenuation() == pytest.approx(12.346)
    attenuator.set_attenuation(99.0)
    assert attenuator.get_attenuation() == 60.0


# ---------------------------------------------------------------------------
# Discovery and config round-trip
# ---------------------------------------------------------------------------


def test_missing_type_refreshes_a_stale_live_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server started before an instrument type was added can load it on demand."""
    from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams

    stale_map = {
        type_name: info
        for type_name, info in resource_catalog.get_type_to_module_map().items()
        if type_name != "prologix_gpib"
    }
    stale_loaded = dict(resource_catalog._loaded_params["instrument"])
    stale_loaded.pop("prologix_gpib", None)
    monkeypatch.setitem(resource_catalog._source_maps, "instrument", stale_map)
    monkeypatch.setitem(resource_catalog._loaded_params, "instrument", stale_loaded)

    assert resource_catalog.load_params_class("prologix_gpib") is PrologixGPIBParams
    assert "prologix_gpib" in resource_catalog.get_type_to_module_map()


def test_scan_gpib_finds_the_simulated_mainframe_over_the_wire(rig) -> None:
    """Discovery uses the same ``*IDN?`` walk it uses on real hardware."""
    controller = rig.gpib_params().create_inst()
    result = Sim900Params._scan_gpib(NoParams(), controller)
    assert [c.key_fields["gpib_address"] for c in result.found] == [str(GPIB_ADDRESS)]
    assert "SIMULATED" in (result.found[0].idn or "")


def test_server_discovery_releases_a_rack_opened_only_for_the_scan(tmp_path: Path, rig) -> None:
    config_dir = tmp_path / "config"
    save_instruments_to_config({rig.gpib: rig.gpib_params()}, config_dir)
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    server = WireServer(bind="inproc://unused", registry=registry)

    result = server.discover(
        type="sim900",
        action="scan_gpib",
        parent_chain=[{"type": "prologix_gpib", "key": rig.gpib}],
    )

    assert result["found"][0]["key_fields"]["gpib_address"] == str(GPIB_ADDRESS)
    assert registry.held_roots() == set()


def test_server_discovery_preserves_a_rack_that_was_already_open(tmp_path: Path, rig) -> None:
    config_dir = tmp_path / "config"
    save_instruments_to_config({rig.gpib: rig.gpib_params()}, config_dir)
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    server = WireServer(bind="inproc://unused", registry=registry)
    root_path = f"inst://{rig.gpib}"
    existing = registry.resolve(root_path)

    server.discover(
        type="sim900",
        action="scan_gpib",
        parent_chain=[{"type": "prologix_gpib", "key": rig.gpib}],
    )

    assert registry.resolve(root_path) is existing
    assert registry.held_roots() == {root_path}


def test_config_tree_round_trips_and_still_sweeps(tmp_path: Path, rig) -> None:
    """A saved and reloaded config reaches the bench."""
    config_dir = tmp_path / "config"
    rig.write(config_dir, ("source", "meter"))

    reloaded = load_instruments(config_dir)
    assert list(reloaded) == [rig.gpib]

    controller = PrologixGPIB.from_params(reloaded[rig.gpib])
    mainframe = Sim900.from_config(controller, key=rig.mainframe)
    source = Sim928.from_config(mainframe, key=rig.source)
    meter = Sim970.from_config(mainframe, key=rig.meter)

    source.turn_on()
    source.set_voltage(1.0)
    assert meter.channels[0].get_voltage() > 0.0
