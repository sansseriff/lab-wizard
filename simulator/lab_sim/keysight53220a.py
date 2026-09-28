"""A Keysight 53220A counter, answering SCPI on a TCP port.

The lab's own driver (``lab_wizard``'s ``Keysight53220A``) connects to this
exactly as it connects to the real counter, a raw socket on port 5025, so the
emulation sits below the whole driver::

    Keysight53220AChannel     real driver, real Counter behaviour
      Keysight53220A          real CONFigure/trigger/gate sequencing
      pyvisa TCPIP SOCKET     real VISA session
      ----------------------  TCP, 127.0.0.1
      Keysight53220A (here)   SCPI parsing, per-input state
      SnspdModel              the physics

Commands are *parsed*, not pattern-matched loosely, and an unrecognised one is
recorded and answered with silence — a driver that starts sending malformed
SCPI fails a test instead of quietly reading a plausible number. Silence is
also what the real instrument gives: the query times out.

The emulation is deliberately literal about the two behaviours that shape the
driver, because those are the ones worth testing against:

* ``CONFigure`` re-enables auto-level, discarding the trigger threshold, and
  resets the trigger source to IMMediate. A driver that does not re-apply its
  input conditioning afterwards will measure at 50% of peak-to-peak here, and
  the counts will say so.
* ``READ?`` returns ``TRIGger:COUNt x SAMPle:COUNt`` readings, comma separated,
  in ASCII — so a driver that assumes one number per read has a test that
  disagrees with it.

What it does not emulate: external triggering waits for nothing (there is no
signal generator in the simulation, so a triggered read returns immediately),
and the gate source is recorded but does not gate. Both are recorded state that
tests can assert on, not physics.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from lab_sim.snspd import SnspdModel

logger = logging.getLogger("lab_sim.keysight53220a")

# ``CONF:TOT:TIM <gate>,(@2)`` / ``CONF:TOT:CONT (@1)`` / ``CONF:FREQ (@1)``
_CHANNEL_RE = re.compile(r"\(@(\d)\)")
# ``INP2:LEV:REL 30`` -> channel 2, tail "LEV:REL", argument "30"
_INPUT_RE = re.compile(r"^INP(?P<channel>[12])?:(?P<tail>[A-Z:]+)\s*(?P<argument>\S*)$")

# The counter's own not-a-number, returned for a level that does not apply.
NOT_A_NUMBER = "+9.91000000000000E+037"


class CounterInput:
    """Per-input state: everything the INPut subsystem can set on one channel."""

    def __init__(self) -> None:
        self.threshold_v = 0.0
        self.auto_level = True  # a preset counter auto-levels at 50%
        self.relative_percent = 50.0
        self.slope = "POS"
        self.coupling = "AC"
        self.impedance_ohm = 1.0e6
        self.range_v = 5.0
        self.probe_factor = 1
        self.noise_rejection = False
        self.lowpass_filter = False
        self.protection_tripped = False


class Keysight53220A:
    """Stands in for a 53220A wired to a simulated detector.

    Both inputs read the same detector, which is what a real two-channel
    counter watching one device through a splitter would do. The measured
    channel is whichever one ``CONFigure`` selected.
    """

    idn = "Agilent Technologies,53220A,SIMULATED,lab_sim"

    def __init__(self, model: SnspdModel):
        self.model = model
        # Command log and rejects survive a *RST: they are the test's window
        # into the driver, not part of the instrument's state.
        self.history: list[str] = []
        self.unrecognised: list[str] = []
        self._reset_state()

    def _reset_state(self) -> None:
        """Factory defaults — what ``*RST`` and ``SYSTem:PRESet`` return to."""
        self.inputs = {1: CounterInput(), 2: CounterInput()}

        # Measurement configuration, in the counter's own terms.
        self.function = "FREQ"
        self.measured_channel = 1
        self.gate_time_s = 0.1
        self.gate_source = "TIME"
        self.gate_polarity = "NEG"
        self.trigger_source = "IMM"
        self.trigger_slope = "NEG"
        self.trigger_delay_s = 0.0
        self.trigger_count = 1
        self.sample_count = 1
        self.measurement_timeout_s = 1.0

        self.running = False
        self.memory: list[float] = []
        self.errors: list[str] = []

    # -- SCPI ---------------------------------------------------------------

    def handle(self, line: str) -> Optional[str]:
        """Process one command; return a reply, or ``None`` for silence."""
        line = line.strip()
        if not line:
            return None
        self.history.append(line)
        upper = line.upper()

        for prefix, handler in (
            ("*", self._common_command),
            ("SYST", self._system_command),
            ("CONF", self._configure_command),
            ("SENS", self._sense_command),
            ("INP", self._input_command),
            ("TRIG", self._trigger_command),
            ("SAMP", self._sample_command),
            ("DATA", self._data_command),
        ):
            if upper.startswith(prefix):
                return handler(upper)

        if upper in ("INIT", "INIT:IMM"):
            self._acquire()
            self.running = True
            return None
        if upper == "ABOR":
            self.running = False
            return None
        if upper == "READ?":
            self._acquire()
            return self._format(self.memory)
        if upper == "FETC?":
            if not self.memory:
                self.errors.append('-230,"Data corrupt or stale"')
                return NOT_A_NUMBER
            return self._format(self.memory)

        return self._unrecognised(line)

    def _common_command(self, upper: str) -> Optional[str]:
        if upper == "*IDN?":
            return self.idn
        if upper == "*RST":
            self._reset_state()
            return None
        if upper == "*CLS":
            self.errors.clear()
            return None
        if upper == "*TST?":
            return "+0"
        if upper == "*OPC?":
            return "1"
        return self._unrecognised(upper)

    def _system_command(self, upper: str) -> Optional[str]:
        if upper.startswith("SYST:ERR"):
            return self.errors.pop(0) if self.errors else '+0,"No error"'
        if upper.startswith("SYST:TIM"):
            if upper.endswith("?"):
                return f"{self.measurement_timeout_s:+.6E}"
            self.measurement_timeout_s = _number(upper.split()[-1], self.measurement_timeout_s)
            return None
        if upper.startswith("SYST:PRES"):
            self._reset_state()
            return None
        return self._unrecognised(upper)

    def _configure_command(self, upper: str) -> Optional[str]:
        """CONFigure — and, as on the real box, a partial reset with it."""
        match = _CHANNEL_RE.search(upper)
        self.measured_channel = int(match.group(1)) if match else 1

        if upper.startswith("CONF:TOT:TIM"):
            self.function = "TOT"
            self.gate_source = "TIME"
            body = upper[len("CONF:TOT:TIM"):].split("(@")[0].strip().rstrip(",")
            if body:
                self.gate_time_s = _number(body, self.gate_time_s)
        elif upper.startswith("CONF:TOT:CONT"):
            self.function = "TOT"
            self.gate_source = "TIME"
            self.gate_time_s = float("inf")
        elif upper.startswith("CONF:FREQ"):
            self.function = "FREQ"
        else:
            return self._unrecognised(upper)

        # What CONFigure takes away, and the reason the driver re-applies it.
        self.trigger_source = "IMM"
        self.trigger_delay_s = 0.0
        self.trigger_count = 1
        self.sample_count = 1
        for channel in self.inputs.values():
            channel.auto_level = True
            channel.relative_percent = 50.0
        self.memory.clear()
        return None

    def _sense_command(self, upper: str) -> Optional[str]:
        if upper.startswith("SENS:TOT:DATA"):
            # A running total, read while a continuous totalize is open. The
            # simulation has no clock of its own, so it answers with one
            # second's worth of counting: the number moves and is of the right
            # size, which is all a caller can use it for.
            return self._format([float(self._counts(1.0))])
        body = upper.removeprefix("SENS:")
        for subsystem in ("TOT:", "FREQ:", "TINT:"):
            body = body.removeprefix(subsystem)
        if body.startswith("GATE:TIME"):
            self.gate_time_s = _number(body.split()[-1], self.gate_time_s)
            return None
        if body.startswith("GATE:SOUR"):
            self.gate_source = body.split()[-1]
            return None
        if body.startswith("GATE:POL"):
            self.gate_polarity = body.split()[-1]
            return None
        return self._unrecognised(upper)

    def _input_command(self, upper: str) -> Optional[str]:
        match = _INPUT_RE.match(upper)
        if not match:
            return self._unrecognised(upper)
        channel = self.inputs[int(match.group("channel") or 1)]
        tail = match.group("tail").rstrip(":")
        argument = match.group("argument")
        # ``INP1:LEV?`` puts the question mark where an argument would be.
        query = argument.startswith("?")
        if query:
            argument = argument[1:].strip()

        if tail == "LEV":
            if query:
                # An input that is not being measured has no threshold to
                # report, exactly as the real counter refuses to invent one.
                if channel is not self.inputs[self.measured_channel]:
                    return NOT_A_NUMBER
                return f"{self._effective_threshold_v(channel):+.6E}"
            channel.threshold_v = _number(argument, channel.threshold_v)
            channel.auto_level = False  # an absolute level turns auto-level off
            return None
        if tail == "LEV:AUTO":
            channel.auto_level = argument in ("ON", "1", "ONCE")
            return None
        if tail == "LEV:REL":
            channel.relative_percent = _number(argument, channel.relative_percent)
            return None
        if tail in ("LEV:MIN", "LEV:MAX", "LEV:PTP"):
            amplitude = self.model.pulse_amplitude() / 1000.0
            return {
                "LEV:MIN": f"{0.0:+.6E}",
                "LEV:MAX": f"{amplitude:+.6E}",
                "LEV:PTP": f"{amplitude:+.6E}",
            }[tail]
        if tail == "SLOP":
            channel.slope = argument or channel.slope
            return None
        if tail == "COUP":
            channel.coupling = argument or channel.coupling
            return None
        if tail == "IMP":
            channel.impedance_ohm = _number(argument, channel.impedance_ohm)
            return None
        if tail == "RANG":
            channel.range_v = _number(argument, channel.range_v)
            return None
        if tail == "PROB":
            channel.probe_factor = int(_number(argument, channel.probe_factor))
            return None
        if tail == "NREJ":
            channel.noise_rejection = argument == "ON"
            return None
        if tail == "FILT":
            channel.lowpass_filter = argument == "ON"
            return None
        if tail == "PROT":
            return "0" if not channel.protection_tripped else "1"
        if tail == "PROT:CLE":
            channel.protection_tripped = False
            return None
        return self._unrecognised(upper)

    def _trigger_command(self, upper: str) -> Optional[str]:
        field, _, argument = upper.partition(" ")
        argument = argument.strip()
        if field.startswith("TRIG:SOUR"):
            self.trigger_source = argument
        elif field.startswith("TRIG:SLOP"):
            self.trigger_slope = argument
        elif field.startswith("TRIG:DEL"):
            self.trigger_delay_s = _number(argument, self.trigger_delay_s)
        elif field.startswith("TRIG:COUN"):
            self.trigger_count = int(_number(argument, self.trigger_count))
        else:
            return self._unrecognised(upper)
        return None

    def _sample_command(self, upper: str) -> Optional[str]:
        if upper.startswith("SAMP:COUN"):
            self.sample_count = int(_number(upper.split()[-1], self.sample_count))
            return None
        return self._unrecognised(upper)

    def _data_command(self, upper: str) -> Optional[str]:
        if upper.startswith("DATA:POIN"):
            return f"{len(self.memory):+d}"
        return self._unrecognised(upper)

    # -- measuring ----------------------------------------------------------

    def _acquire(self) -> None:
        """Fill reading memory with one full trigger cycle."""
        readings = max(1, self.trigger_count) * max(1, self.sample_count)
        gate = self.gate_time_s if self.gate_time_s not in (0.0, float("inf")) else 1.0
        if self.function == "FREQ":
            self.memory = [float(self._counts(gate)) / gate for _ in range(readings)]
        else:
            self.memory = [float(self._counts(gate)) for _ in range(readings)]

    def _counts(self, gate_time: float) -> int:
        """Events the detector delivers through this input's discriminator."""
        channel = self.inputs[self.measured_channel]
        threshold_mV = self._effective_threshold_v(channel) * 1000.0
        return self.model.count_events(gate_time, threshold_mV)

    def _effective_threshold_v(self, channel: CounterInput) -> float:
        """The level actually in force, auto-level included.

        Auto-level puts the threshold at a percentage of the pulse height, so a
        driver that lets ``CONFigure`` re-enable it ends up triggering at 50% of
        the pulse rather than where the experiment asked — visible here as a
        count rate that ignores the configured threshold.
        """
        if not channel.auto_level:
            # The hardware quantizes; so does the simulation, or a driver could
            # pass this test while sending levels the real counter would round.
            step = channel.range_v / 2000.0
            return round(channel.threshold_v / step) * step
        return self.model.pulse_amplitude() / 1000.0 * channel.relative_percent / 100.0

    # -- helpers ------------------------------------------------------------

    def _format(self, readings: list[float]) -> str:
        return ",".join(f"{value:+.15E}" for value in readings)

    def _unrecognised(self, line: str) -> None:
        self.unrecognised.append(line)
        logger.warning("53220A (simulated): unrecognised command %r", line)
        return None


def _number(text: str, default: float) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        logger.warning("53220A (simulated): unparseable number %r", text)
        return default
