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
* the wiring — that the rack and the counter on the simulated bench really do
  watch one detector;
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

from lab_procedure import Point, ProcedureRunner, RunStarted, Status

from lab_wizard.sim import SnspdModel, SnspdParams
from lab_wizard.lib.project import Project
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.keysight53220A import Keysight53220A, Keysight53220AChannelParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928
from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    assign_missing_leaf_attribute_names,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest

# The bench's standard detector: its turn-on sits well inside its switching
# current, so the sweep crosses the whole error function before it latches.
DEVICE = SnspdParams(
    critical_current_a=3.0e-7,
    retrapping_current_a=1.5e-7,
    incident_photon_rate_hz=1.0e6,
    max_detection_efficiency=0.8,
    detection_midpoint_current_a=2.0e-7,
    detection_width_current_a=3.0e-8,
    dark_count_rate_hz=100.0,
)

# The detector alone, without the readout's noise, for the tests of its own physics.
QUIET = DEVICE.model_copy(update={"readout_noise_rms_mV": 0.0})

# 100 kΩ bias resistor, so 0.030 V is the 300 nA switching current.
SWEEP_V = [round(0.002 * i, 3) for i in range(17)]  # 0.000 .. 0.032 V
SWITCHING_BIAS_V = 0.030
MIDPOINT_BIAS_V = 0.020
GATE_TIME_S = 0.1
THRESHOLD_MV = -50.0


# ---------------------------------------------------------------------------
# The detector's counting physics
# ---------------------------------------------------------------------------


def _biased_model(bias_v: float, params: SnspdParams | None = None) -> SnspdModel:
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
    rate = _biased_model(0.029, QUIET).count_rate()
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
    model = _biased_model(SWITCHING_BIAS_V + 0.002, QUIET)
    assert model.count_rate() == 0.0
    assert model.count_events(1.0) == 0
    assert model.is_normal


def test_counts_are_poissonian_about_the_rate() -> None:
    model = _biased_model(MIDPOINT_BIAS_V, QUIET)
    rate = model.count_rate()
    draws = [model.count_events(0.001) for _ in range(200)]
    mean = sum(draws) / len(draws)
    variance = sum((d - mean) ** 2 for d in draws) / len(draws)

    assert mean == pytest.approx(rate * 0.001, rel=0.1)
    # The signature of a Poisson process: variance equals the mean.
    assert variance == pytest.approx(mean, rel=0.4)
    assert len(set(draws)) > 1, "a drawn count must not be a constant"


def test_the_discriminator_cuts_counts_off_above_the_pulse_height() -> None:
    model = _biased_model(0.029, QUIET)
    full = model.count_rate(threshold_mV=THRESHOLD_MV)

    assert model.count_rate(threshold_mV=model.pulse_amplitude()) == pytest.approx(
        full / 2, rel=1e-9
    ), "half the pulses clear a threshold at the mean pulse height"
    assert model.count_rate(threshold_mV=400.0) < full / 1000
    # Magnitude only: the model takes no position on readout polarity.
    assert model.count_rate(threshold_mV=-100.0) == model.count_rate(threshold_mV=100.0)


def test_a_trigger_near_the_readout_noise_counts_it_whatever_the_bias() -> None:
    """The noise floor a PCR curve's lowest trigger level shows."""
    for bias in (0.0, 0.010, SWITCHING_BIAS_V + 0.002):  # unbiased, below turn-on, latched
        model = _biased_model(bias)
        assert model.count_rate(25.0) > 50_000
        assert model.count_rate(25.0) == pytest.approx(model.noise_count_rate(25.0), rel=0.05)
    # Rice's formula falls off as exp(-T²/2σ²): a few σ up, there is none.
    assert _biased_model(0.0).noise_count_rate(50.0) < 0.1
    assert _biased_model(0.0).count_rate(90.0) < 1e-6


def test_pulses_grow_with_bias_so_a_higher_trigger_turns_on_later() -> None:
    """Why PCR curves are measured at several trigger levels."""
    assert _biased_model(0.015).pulse_amplitude() == pytest.approx(DEVICE.pulse_amplitude_mV / 2)
    low, high = 50.0, 150.0
    # Well biased, both triggers see nearly every pulse...
    top = _biased_model(0.028)
    assert top.count_rate(high) / top.count_rate(low) > 0.95
    # ...lightly biased, the high one cuts off the small pulses.
    weak = _biased_model(0.018)  # 120 mV pulses
    assert weak.count_rate(high) < weak.count_rate(low) / 5


# ---------------------------------------------------------------------------
# The counter driver against the simulated counter
# ---------------------------------------------------------------------------


def _counter_params(rig) -> Any:
    params = rig.counter_params()
    params.channels = {
        0: Keysight53220AChannelParams(threshold_mV=THRESHOLD_MV, coupling="DC", gate_time_s=GATE_TIME_S)
    }
    return params


def _counter(rig) -> Keysight53220A:
    return cast(Keysight53220A, _counter_params(rig).create_inst())


def _bias(rig, volts: float) -> None:
    """Bias the detector directly, as if a source were already set."""
    rig.model.set_output_enabled(True)
    rig.model.set_bias_voltage(volts)


def test_the_driver_speaks_scpi_the_simulated_counter_understands(rig) -> None:
    counter = _counter(rig)
    _bias(rig, 0.029)

    assert isinstance(counter[0], Counter)
    counts = counter[0].count(GATE_TIME_S)

    simulated = rig.bench.counter
    assert counts > 0
    assert simulated.unrecognised == [], "the driver sent SCPI the counter rejected"
    assert simulated.function == "TOT"
    assert simulated.measured_channel == 1


def test_the_configured_threshold_survives_the_drivers_own_configure(rig) -> None:
    """The failure this whole arrangement exists to catch.

    ``CONFigure`` re-enables auto-level on the real instrument, and the
    simulated one does the same. If the driver did not re-apply the input
    conditioning afterwards, the counter would be triggering at 50% of the
    pulse height with nothing to say so.
    """
    counter = _counter(rig)
    counter[0].count(GATE_TIME_S)

    channel = rig.bench.counter.inputs[1]
    assert channel.auto_level is False
    assert channel.threshold_v == pytest.approx(THRESHOLD_MV / 1000.0)
    assert counter[0].get_threshold() == pytest.approx(THRESHOLD_MV)


def test_raising_the_threshold_past_the_pulse_height_stops_the_counts(rig) -> None:
    counter = _counter(rig)
    _bias(rig, 0.029)

    counting = counter[0].count(GATE_TIME_S)
    counter[0].set_threshold(DEVICE.pulse_amplitude_mV * 2)
    silent = counter[0].count(GATE_TIME_S)

    assert counting > 1000
    assert silent == 0


def test_a_multi_reading_trigger_cycle_comes_back_reading_by_reading(rig) -> None:
    counter = _counter(rig)
    _bias(rig, 0.029)
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
# Wiring: one detector, two transports
# ---------------------------------------------------------------------------


def test_the_source_and_the_counter_share_one_detector(rig) -> None:
    gpib = rig.gpib_params(("source",)).create_inst()
    source = cast(Sim928, gpib.make_child(rig.mainframe).make_child(rig.source))
    counter = _counter(rig)
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


# ---------------------------------------------------------------------------
# The generated project, end to end
# ---------------------------------------------------------------------------


def _generate_project(tmp_path: Path, rig) -> dict[str, Any]:
    config_dir = tmp_path / "config"
    instruments = {**rig.instruments(("source",)), rig.counter: _counter_params(rig)}
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)

    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=tmp_path / "projects",
        req=GenerateProjectRequest(
            measurement_name="pcr_curve",
            kind="procedure",
            selected_resources=[rig.select("voltage_source", "source"), rig.select("counter", "counter")],
            project_prefix="pcr_sim",
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


def test_generated_setup_wires_the_simulated_counter(tmp_path: Path, rig) -> None:
    out = _generate_project(tmp_path, rig)
    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    measurement_text = Path(out["measurement_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    ast.parse(measurement_text)

    assert "class Resources(measurement.PcrCurveResources):" in setup_text
    assert "    counter: Keysight53220AChannel\n" in setup_text and "    voltage_source: Sim928\n" in setup_text
    assert "def build_pcr_curve_procedure(resources: PcrCurveResources)" in measurement_text
    config = load_instruments(tmp_path / "config")
    counter_name = config[rig.counter].channels[0].attribute_name
    source_name = config[rig.gpib].children[rig.mainframe].children[rig.source].attribute_name

    payload = cast(
        dict[str, Any],
        YAML(typ="safe").load(Path(out["yaml_file"]).read_text(encoding="utf-8")),
    )
    assert payload["roles"] == {"voltage_source": source_name, "counter": counter_name}


def test_generated_project_measures_the_simulated_pcr_curve(tmp_path: Path, rig) -> None:
    out = _generate_project(tmp_path, rig)
    module = _load_setup_module(Path(out["setup_file"]))
    resources = Project.load(Path(out["project_dir"])).resources(module.Resources)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Point] = []
    runner.context.data_bus.subscribe(Point, observations.append)

    status = runner.run(
        module.measurement.build_pcr_curve_procedure(resources),
        RunStarted(procedure="pcr_curve", params=resources.params.model_dump(mode="json")),
    )
    assert status is Status.SUCCESS
    assert len(observations) == len(SWEEP_V)

    for observation, bias in zip(observations, SWEEP_V):
        data = observation.values
        assert data["bias_voltage"] == pytest.approx(bias)
        assert data["int_time"] == GATE_TIME_S
        assert data["count_rate"] == pytest.approx(data["counts"] / GATE_TIME_S)

        # Every point within counting statistics of what the model would give.
        expected = _expected_counts(bias)
        tolerance = 5.0 * math.sqrt(expected) + 5.0
        assert abs(data["counts"] - expected) <= tolerance, (
            f"at {bias} V: {data['counts']} counts, model says {expected:.0f}"
        )


def test_a_threshold_left_behind_by_another_caller_does_not_leak_in(tmp_path: Path, rig) -> None:
    """The run sets its own threshold instead of inheriting the counter's.

    On a server-held counter, the current threshold is whatever the previous
    client set. Leave it far above the pulse height — which alone would count
    nothing — and the curve must still match the model at the project's own
    threshold.
    """
    out = _generate_project(tmp_path, rig)
    module = _load_setup_module(Path(out["setup_file"]))
    resources = Project.load(Path(out["project_dir"])).resources(module.Resources)
    resources.counter.set_threshold(DEVICE.pulse_amplitude_mV * 2)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Point] = []
    runner.context.data_bus.subscribe(Point, observations.append)
    assert runner.run(module.measurement.build_pcr_curve_procedure(resources)) is Status.SUCCESS

    assert resources.counter.get_threshold() == pytest.approx(THRESHOLD_MV)
    top = observations[-1].values
    expected = _expected_counts(SWEEP_V[-1])
    assert abs(top["counts"] - expected) <= 5.0 * math.sqrt(expected) + 5.0


def test_the_measured_curve_has_the_shape_of_a_pcr_curve(tmp_path: Path, rig) -> None:
    out = _generate_project(tmp_path, rig)
    module = _load_setup_module(Path(out["setup_file"]))
    resources = Project.load(Path(out["project_dir"])).resources(module.Resources)

    runner = ProcedureRunner(instruments=resources)
    observations: list[Point] = []
    runner.context.data_bus.subscribe(Point, observations.append)
    runner.run(module.measurement.build_pcr_curve_procedure(resources))

    counts = {o.values["bias_voltage"]: o.values["counts"] for o in observations}
    plateau = DEVICE.incident_photon_rate_hz * DEVICE.max_detection_efficiency * GATE_TIME_S

    assert counts[0.0] == 0, "no bias, no clicks"
    assert counts[0.010] < plateau / 1000, "below the turn-on, dark counts only"
    # The error function's defining point: half the plateau at the midpoint.
    assert counts[MIDPOINT_BIAS_V] == pytest.approx(plateau / 2, rel=0.05)
    assert counts[0.028] == pytest.approx(plateau, rel=0.05), "saturated"
    assert counts[SWITCHING_BIAS_V] == 0, "the detector latches at its switching current"

    turn_on = [counts[v] for v in SWEEP_V if 0.010 <= v <= MIDPOINT_BIAS_V]
    assert all(a < b for a, b in zip(turn_on, turn_on[1:])), "monotonic through the knee"


def test_generated_setup_runs_as_a_script(tmp_path: Path, rig) -> None:
    """The generated file is meant to be run, not only imported."""
    out = _generate_project(tmp_path, rig)
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


def test_generated_setup_refuses_a_counter_another_process_has_claimed(tmp_path: Path, rig) -> None:
    """The script claims its transports before opening anything.

    This test process holds the counter's lease, and it is alive and is not the
    script — so the script must stop with the holder's name rather than open
    the counter underneath it.
    """
    from lab_wizard.lib.client import leases

    out = _generate_project(tmp_path, rig)
    setup_path = Path(out["setup_file"])
    counter_key = _counter_params(rig).transport_key()
    assert counter_key
    leases.acquire(counter_key, owner="a measurement already running")
    try:
        result = subprocess.run(
            [sys.executable, str(setup_path)],
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(setup_path.parent),
        )
    finally:
        leases.release(counter_key)

    assert result.returncode != 0
    assert "a measurement already running" in result.stderr
