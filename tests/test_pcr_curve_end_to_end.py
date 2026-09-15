"""The simulated PCR stack: detector physics, counter driver, generated project.

A photon-count-rate curve needs two instruments to agree about one detector —
the source biases it, the counter counts its clicks — and they sit on separate
transports, so this is also the test that the simulated lab can be *wired*.

Four layers, each checked where it can fail:

* the detector model's counting physics — the error-function turn-on, dark
  counts, the discriminator, and the collapse at latching;
* the wire protocol — that the real ``Keysight53220A`` driver, unmodified,
  produces SCPI the simulated counter understands, and that what a
  ``CONFigure`` discards is put back;
* the wiring — that a ``fake900`` and a ``fake_counter`` naming the same
  detector really do share one;
* the whole loop — a wizard-generated PCR project run against the simulation,
  with the measured curve checked against the model's own physics.
"""

from __future__ import annotations

import ast
import importlib.util
import math
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from ruamel.yaml import YAML

from lab_procedure import Observation, ProcedureRunner, RunStarted, Status

from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900Params
from lab_wizard.lib.instruments.fake_rack.fake_counter import (
    FakeCounter,
    FakeCounterParams,
)
from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpib, FakeGpibParams
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928, Fake928Params
from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams
from lab_wizard.lib.instruments.fake_rack.wiring import reset_detectors
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.measurements.pcr_curve.pcr_curve import PCRCurveMeasurement
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities.model_tree import load_project_config
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)

PORT = "sim://pcr-rack"
COUNTER_ADDRESS = "sim://pcr-counter"
GPIB_ADDRESS = "5"
SOURCE_SLOT = "1"
DETECTOR = "snspd-pcr"

GPIB_KEY = instrument_hash("fakegpib", PORT)
MAINFRAME_KEY = instrument_hash("fake900", GPIB_ADDRESS)
SOURCE_KEY = instrument_hash("fake928", SOURCE_SLOT)
COUNTER_KEY = instrument_hash("fake_counter", f"{COUNTER_ADDRESS}:5025")

# A detector whose turn-on sits well inside its switching current, so the sweep
# crosses the whole error function before the device latches.
DEVICE = SnspdModelParams(
    critical_current_a=3.0e-7,
    retrapping_current_a=1.5e-7,
    incident_photon_rate_hz=1.0e6,
    max_detection_efficiency=0.8,
    detection_midpoint_current_a=2.0e-7,
    detection_width_current_a=3.0e-8,
    dark_count_rate_hz=100.0,
)

# 100 kΩ bias resistor, so 0.030 V is the 300 nA switching current.
SWEEP_V = [round(0.002 * i, 3) for i in range(17)]  # 0.000 .. 0.032 V
SWITCHING_BIAS_V = 0.030
MIDPOINT_BIAS_V = 0.020
GATE_TIME_S = 0.1
THRESHOLD_MV = -50.0


@pytest.fixture(autouse=True)
def _cold_lab() -> None:
    """Every test gets an untouched detector, so counts are reproducible."""
    reset_detectors()


# ---------------------------------------------------------------------------
# The detector's counting physics
# ---------------------------------------------------------------------------


def _biased_model(bias_v: float, params: SnspdModelParams | None = None) -> SnspdModel:
    model = SnspdModel(params or DEVICE)
    model.set_output_enabled(True)
    model.set_bias_voltage(bias_v)
    return model


def test_detection_efficiency_is_an_error_function_turn_on() -> None:
    plateau = DEVICE.max_detection_efficiency

    assert _biased_model(0.005).detection_efficiency() == pytest.approx(0.0, abs=1e-6)
    # Half the plateau at the midpoint current is what "midpoint" means.
    assert _biased_model(MIDPOINT_BIAS_V).detection_efficiency() == pytest.approx(
        plateau / 2, rel=1e-9
    )
    # One width above and below are symmetric about that half point.
    below = _biased_model(0.017).detection_efficiency()
    above = _biased_model(0.023).detection_efficiency()
    assert below + above == pytest.approx(plateau, rel=1e-9)
    assert _biased_model(0.029).detection_efficiency() == pytest.approx(plateau, rel=1e-2)


def test_count_rate_saturates_at_the_incident_rate_times_efficiency() -> None:
    plateau = DEVICE.incident_photon_rate_hz * DEVICE.max_detection_efficiency
    rate = _biased_model(0.029).count_rate()
    assert rate == pytest.approx(plateau, rel=0.02)


def test_dark_counts_are_negligible_under_the_plateau_and_rise_toward_switching() -> None:
    model = _biased_model(0.010)
    quiet = model.dark_count_rate()

    assert quiet < DEVICE.dark_count_rate_hz / 100
    # The configured rate is the rate at the critical current, by definition.
    assert model.dark_count_rate(DEVICE.critical_current_a) == pytest.approx(
        DEVICE.dark_count_rate_hz, rel=1e-9
    )
    # Doubling every dark_count_doubling_current_a, by construction.
    doubling_v = DEVICE.dark_count_doubling_current_a * 100_000.0
    assert _biased_model(0.010 + doubling_v).dark_count_rate() == pytest.approx(
        2 * quiet, rel=1e-9
    )


def test_a_latched_detector_counts_nothing() -> None:
    model = _biased_model(SWITCHING_BIAS_V + 0.002)
    assert model.count_rate() == 0.0
    assert model.count_events(1.0) == 0
    assert model.is_normal


def test_counts_are_poissonian_about_the_rate() -> None:
    model = _biased_model(MIDPOINT_BIAS_V)
    rate = model.count_rate()
    draws = [model.count_events(0.001) for _ in range(200)]
    mean = sum(draws) / len(draws)
    variance = sum((d - mean) ** 2 for d in draws) / len(draws)

    assert mean == pytest.approx(rate * 0.001, rel=0.1)
    # The signature of a Poisson process: variance equals the mean.
    assert variance == pytest.approx(mean, rel=0.4)
    assert len(set(draws)) > 1, "a drawn count must not be a constant"


def test_the_discriminator_cuts_counts_off_above_the_pulse_height() -> None:
    model = _biased_model(0.029)
    full = model.count_rate(threshold_mV=THRESHOLD_MV)

    assert model.count_rate(threshold_mV=DEVICE.pulse_amplitude_mV) == pytest.approx(
        full / 2, rel=1e-9
    ), "half the pulses clear a threshold at the mean pulse height"
    assert model.count_rate(threshold_mV=400.0) < full / 1000
    # Magnitude only: the model takes no position on readout polarity.
    assert model.count_rate(threshold_mV=-100.0) == model.count_rate(threshold_mV=100.0)


# ---------------------------------------------------------------------------
# The counter driver against the simulated counter
# ---------------------------------------------------------------------------


def _counter_params(**overrides: Any) -> FakeCounterParams:
    from lab_wizard.lib.instruments.keysight53220A import Keysight53220AChannelParams

    fields: dict[str, Any] = {
        "ip_address": COUNTER_ADDRESS,
        "detector_name": DETECTOR,
        "device": DEVICE,
        "channels": {
            0: Keysight53220AChannelParams(
                threshold_mV=THRESHOLD_MV, coupling="DC", gate_time_s=GATE_TIME_S
            )
        },
    }
    return FakeCounterParams(**{**fields, **overrides})


def test_the_driver_speaks_scpi_the_simulated_counter_understands() -> None:
    counter = cast(FakeCounter, _counter_params().create_inst())
    counter.virtual.model.set_output_enabled(True)
    counter.virtual.model.set_bias_voltage(0.029)

    assert isinstance(counter[0], Counter)
    counts = counter[0].count(GATE_TIME_S)

    assert counts > 0
    assert counter.virtual.unrecognised == [], "the driver sent SCPI the counter rejected"
    assert counter.virtual.function == "TOT"
    assert counter.virtual.measured_channel == 1


def test_the_configured_threshold_survives_the_drivers_own_configure() -> None:
    """The failure this whole arrangement exists to catch.

    ``CONFigure`` re-enables auto-level on the real instrument, and the
    simulated one does the same. If the driver did not re-apply the input
    conditioning afterwards, the counter would be triggering at 50% of the
    pulse height with nothing to say so.
    """
    counter = cast(FakeCounter, _counter_params().create_inst())
    counter[0].count(GATE_TIME_S)

    channel = counter.virtual.inputs[1]
    assert channel.auto_level is False
    assert channel.threshold_v == pytest.approx(THRESHOLD_MV / 1000.0)
    assert counter[0].get_threshold() == pytest.approx(THRESHOLD_MV)


def test_raising_the_threshold_past_the_pulse_height_stops_the_counts() -> None:
    counter = cast(FakeCounter, _counter_params().create_inst())
    counter.virtual.model.set_output_enabled(True)
    counter.virtual.model.set_bias_voltage(0.029)

    counting = counter[0].count(GATE_TIME_S)
    counter[0].set_threshold(DEVICE.pulse_amplitude_mV * 2)
    silent = counter[0].count(GATE_TIME_S)

    assert counting > 1000
    assert silent == 0


def test_a_multi_reading_trigger_cycle_comes_back_reading_by_reading() -> None:
    counter = cast(FakeCounter, _counter_params().create_inst())
    counter.virtual.model.set_output_enabled(True)
    counter.virtual.model.set_bias_voltage(0.029)
    counter.configure_trigger(trigger_count=2, sample_count=3)

    readings = counter[0].read_counts(0.01)

    # Six separate gates, kept apart — the shape a pulsed run needs, where one
    # sample per trigger sees the light and the others measure dark counts.
    assert len(readings) == 6
    per_gate = _biased_model(0.029).count_rate(THRESHOLD_MV) * 0.01
    for reading in readings:
        assert abs(reading - per_gate) <= 5.0 * math.sqrt(per_gate) + 5.0
    assert counter[0].count(0.01) > per_gate * 5, "count() totals the whole cycle"


# ---------------------------------------------------------------------------
# Wiring: one detector, two roots
# ---------------------------------------------------------------------------


def _rack_and_counter() -> tuple[Fake928, FakeCounter]:
    rack_params = FakeGpibParams(
        port=PORT,
        children={
            MAINFRAME_KEY: Fake900Params(
                gpib_address=GPIB_ADDRESS,
                device=DEVICE,
                detector_name=DETECTOR,
                children={SOURCE_KEY: Fake928Params(slot=SOURCE_SLOT)},
            )
        },
    )
    gpib = FakeGpib.from_params(rack_params)
    from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900

    mainframe = Fake900.from_config(gpib, key=MAINFRAME_KEY)
    source = cast(Fake928, Fake928.from_config(mainframe, key=SOURCE_KEY))
    counter = cast(FakeCounter, _counter_params().create_inst())
    return source, counter


def test_the_source_and_the_counter_share_one_detector() -> None:
    source, counter = _rack_and_counter()
    source.turn_on()

    source.set_voltage(0.005)
    quiet = counter[0].count(GATE_TIME_S)
    source.set_voltage(0.029)
    lit = counter[0].count(GATE_TIME_S)
    source.set_voltage(SWITCHING_BIAS_V + 0.002)
    latched = counter[0].count(GATE_TIME_S)

    assert quiet < 100, "below the turn-on, only dark counts"
    assert lit > 50_000, "on the plateau, the counter sees the photon rate"
    assert latched == 0, "a switched detector stops clicking"


def test_an_unnamed_detector_is_private_and_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The default must never wire two instruments together by accident.

    It must also not fail silently: an unwired counter reads zero at every
    bias, which looks exactly like a dead detector.
    """
    source, _ = _rack_and_counter()
    with caplog.at_level("WARNING"):
        lonely = cast(FakeCounter, _counter_params(detector_name="").create_inst())

    source.turn_on()
    source.set_voltage(0.029)

    assert lonely[0].count(GATE_TIME_S) == 0, "an unbiased detector emits nothing"
    assert "no detector_name" in caplog.text


# ---------------------------------------------------------------------------
# The generated project, end to end
# ---------------------------------------------------------------------------


def _write_config(config_dir: Path) -> None:
    """The config tree a user would build in Manage Instruments."""
    instruments = {
        GPIB_KEY: FakeGpibParams(
            port=PORT,
            children={
                MAINFRAME_KEY: Fake900Params(
                    gpib_address=GPIB_ADDRESS,
                    device=DEVICE,
                    detector_name=DETECTOR,
                    children={SOURCE_KEY: Fake928Params(slot=SOURCE_SLOT)},
                )
            },
        ),
        COUNTER_KEY: _counter_params(),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


def _generate_project(tmp_path: Path) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_config(config_dir)

    out = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateProjectRequest(
            measurement_name="pcr_curve",
            selected_resources=[
                SelectedResource(
                    variable_name="voltage_source",
                    type="fake928",
                    key=SOURCE_KEY,
                    path=[
                        SelectedNodeRef(type="fake928", key=SOURCE_KEY),
                        SelectedNodeRef(type="fake900", key=MAINFRAME_KEY),
                        SelectedNodeRef(type="fakegpib", key=GPIB_KEY),
                    ],
                ),
                SelectedResource(
                    variable_name="counter",
                    type="fake_counter",
                    key=COUNTER_KEY,
                    channel_index=0,
                    path=[SelectedNodeRef(type="fake_counter", key=COUNTER_KEY)],
                ),
            ],
            project_prefix="pcr_fake",
        ),
    )
    _set_measurement_params(Path(out["yaml_file"]))
    return out


def _set_measurement_params(yaml_path: Path) -> None:
    """Edit the generated project YAML the way a user tunes a run."""
    yaml = YAML(typ="rt")
    io: Any = yaml
    with yaml_path.open("r", encoding="utf-8") as handle:
        payload = io.load(handle)

    params = payload["measurement"]["params"]
    params["bias"]["sweep"] = {"mode": "explicit", "values_V": list(SWEEP_V)}
    params["bias"]["settle_s"] = 0.0
    params["readout"]["gate_time_s"] = GATE_TIME_S
    params["readout"]["threshold_mV"] = THRESHOLD_MV

    with yaml_path.open("w", encoding="utf-8") as handle:
        io.dump(payload, handle)


def _load_setup_module(setup_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("generated_pcr_setup", setup_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _expected_counts(bias_v: float) -> float:
    """Counts the detector model says a gate at this bias should produce."""
    model = _biased_model(float(f"{bias_v:0.3f}"))  # the SIM928 formats to mV
    return model.count_rate(THRESHOLD_MV) * GATE_TIME_S


def test_generated_setup_wires_the_simulated_counter(tmp_path: Path) -> None:
    out = _generate_project(tmp_path)
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    measurement_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    ast.parse(measurement_text)

    assert "from pcr_curve import PCRCurveMeasurement" in setup_text
    assert "class PCRCurveMeasurement" in measurement_text
    assert (
        "from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounter"
        in setup_text
    )
    assert f"FakeCounter.from_config(resources, key={COUNTER_KEY!r})" in setup_text
    assert ".channels[0]" in setup_text

    payload = cast(
        dict[str, Any],
        YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8")),
    )
    counter = payload["resources"]["instruments"][COUNTER_KEY]
    rack = payload["resources"]["instruments"][GPIB_KEY]
    mainframe = rack["children"][MAINFRAME_KEY]
    # The wiring is stated in the project, not implied by instantiation order.
    assert counter["detector_name"] == mainframe["detector_name"] == DETECTOR
    assert counter["channels"][0]["threshold_mV"] == THRESHOLD_MV


def test_generated_project_measures_the_simulated_pcr_curve(tmp_path: Path) -> None:
    out = _generate_project(tmp_path)
    module = _load_setup_module(Path(out["setup_file"]))
    project = load_project_config(Path(out["yaml_file"]))
    resources = module.create_instrument_resources(project)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, observations.append)

    status = runner.run(
        PCRCurveMeasurement(resources).build_procedure(),
        RunStarted(run_type="pcr_curve", config=resources.params.model_dump(mode="json")),
    )
    assert status is Status.SUCCESS
    assert len(observations) == len(SWEEP_V)

    for observation, bias in zip(observations, SWEEP_V):
        data = observation.data
        assert data["bias_voltage"] == pytest.approx(bias)
        assert data["int_time"] == GATE_TIME_S
        assert data["count_rate"] == pytest.approx(data["counts"] / GATE_TIME_S)

        # Every point within counting statistics of what the model would give.
        expected = _expected_counts(bias)
        tolerance = 5.0 * math.sqrt(expected) + 5.0
        assert abs(data["counts"] - expected) <= tolerance, (
            f"at {bias} V: {data['counts']} counts, model says {expected:.0f}"
        )


def test_a_threshold_left_behind_by_another_caller_does_not_leak_in(tmp_path: Path) -> None:
    """The run sets its own threshold instead of inheriting the counter's.

    On a server-held counter, the current threshold is whatever the previous
    client set. Leave it far above the pulse height — which alone would count
    nothing — and the curve must still match the model at the project's own
    threshold.
    """
    out = _generate_project(tmp_path)
    module = _load_setup_module(Path(out["setup_file"]))
    project = load_project_config(Path(out["yaml_file"]))
    resources = module.create_instrument_resources(project)
    resources.counter.set_threshold(DEVICE.pulse_amplitude_mV * 2)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, observations.append)
    assert runner.run(PCRCurveMeasurement(resources).build_procedure()) is Status.SUCCESS

    assert resources.counter.get_threshold() == pytest.approx(THRESHOLD_MV)
    top = observations[-1].data
    expected = _expected_counts(SWEEP_V[-1])
    assert abs(top["counts"] - expected) <= 5.0 * math.sqrt(expected) + 5.0


def test_the_measured_curve_has_the_shape_of_a_pcr_curve(tmp_path: Path) -> None:
    out = _generate_project(tmp_path)
    module = _load_setup_module(Path(out["setup_file"]))
    project = load_project_config(Path(out["yaml_file"]))
    resources = module.create_instrument_resources(project)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Observation] = []
    runner.context.data_bus.subscribe(Observation, observations.append)
    runner.run(PCRCurveMeasurement(resources).build_procedure())

    counts = {o.data["bias_voltage"]: o.data["counts"] for o in observations}
    plateau = DEVICE.incident_photon_rate_hz * DEVICE.max_detection_efficiency * GATE_TIME_S

    assert counts[0.0] == 0, "no bias, no clicks"
    assert counts[0.010] < plateau / 1000, "below the turn-on, dark counts only"
    # The error function's defining point: half the plateau at the midpoint.
    assert counts[MIDPOINT_BIAS_V] == pytest.approx(plateau / 2, rel=0.05)
    assert counts[0.028] == pytest.approx(plateau, rel=0.05), "saturated"
    assert counts[SWITCHING_BIAS_V] == 0, "the detector latches at its switching current"

    turn_on = [counts[v] for v in SWEEP_V if 0.010 <= v <= MIDPOINT_BIAS_V]
    assert all(a < b for a, b in zip(turn_on, turn_on[1:])), "monotonic through the knee"


def test_generated_setup_runs_as_a_script(tmp_path: Path) -> None:
    """The generated file is meant to be run, not only imported."""
    out = _generate_project(tmp_path)
    setup_path = Path(out["setup_file"])

    result = subprocess.run(
        [sys.executable, str(setup_path)],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=str(setup_path.parent),
    )
    assert result.returncode == 0, result.stderr
    assert "Traceback" not in result.stderr
