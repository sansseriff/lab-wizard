"""A SIM900 rack behind a Prologix GPIB controller, as a byte stream.

The lab's drivers reach a real rack through a Prologix GPIB-USB controller,
which the computer sees as a serial port. The simulator offers a serial port
too (a pseudo-terminal, see :mod:`lab_sim.transports`), so the whole production
stack sits above it::

    Sim928 / Sim970          real driver, real commands
      Sim900SlotDep          real CONN/esc framing
      PrologixControllerDep  real ++addr, auto-read semantics
      pyserial               real serial port, opened exclusively
      ---------------------  pseudo-terminal
      PrologixController     (here) ++ commands, GPIB addressing
      GpibBus                devices by GPIB address
      Sim900                 slot routing
      Sim928 / Sim970        the modules
      SnspdModel             the physics

Everything the drivers send is *parsed*, not pattern-matched loosely: a
command this module does not recognise gets no reply and is recorded, so a
driver that starts sending malformed commands fails a test rather than quietly
reading a plausible number.

The emulation is deliberately literal about the Prologix controller's
auto-read behaviour (``++auto 1``): a query writes and then reads one line
back from the same byte stream, with no addressing information in the reply.
An unrecognised command therefore produces *silence*, and the driver sees a
read timeout — the same symptom real hardware gives.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

from lab_sim.snspd import SnspdModel

logger = logging.getLogger("lab_sim.sim900")

# ``CONN <slot>, "<escape>"`` — how a SIM900 is told to route the GPIB
# connection into one module until the escape string comes back.
_CONN_RE = re.compile(r'^CONN\s+(\d+)\s*,\s*"([^"]*)"$', re.IGNORECASE)
# Prologix controller commands are the ones starting with ``++``.
_CONTROLLER_RE = re.compile(r"^\+\+(\w+)\s*(.*)$")

# Real identification strings, so the lab's own scans recognise the rack, with
# SIMULATED where the serial number goes so nobody mistakes it for hardware.
SIM900_IDN = "Stanford_Research_Systems,SIM900,s/n SIMULATED,ver3.6"
SIM928_IDN = "Stanford_Research_Systems,SIM928,s/n SIMULATED,ver2.2"
SIM970_IDN = "Stanford_Research_Systems,SIM970,s/n SIMULATED,ver2.0"
PROLOGIX_VERSION = "Prologix GPIB-USB Controller version 6.107 (lab_sim)"


class LineDevice(ABC):
    """Something that answers one command line at a time."""

    @abstractmethod
    def handle(self, line: str) -> Optional[str]:
        """Process one command line; return a reply, or ``None`` for silence."""


# --------------------------------------------------------------------------
# Modules
# --------------------------------------------------------------------------


class Sim928(LineDevice):
    """A SIM928 isolated voltage source, biasing the detector.

    Its output is not a number kept for its own sake: it is the bias applied to
    the shared :class:`SnspdModel`, which is what makes the voltmeter in another
    slot read something meaningful.
    """

    def __init__(self, model: SnspdModel):
        self.model = model
        self.voltage = 0.0
        self.output_on = False

    def handle(self, line: str) -> Optional[str]:
        upper = line.upper()

        if upper == "*IDN?":
            return SIM928_IDN
        if upper == "VOLT?":
            return f"{self.voltage:.3f}"
        if upper == "EXON?":
            return "1" if self.output_on else "0"
        if upper == "OPON":
            self.output_on = True
            self.model.set_output_enabled(True)
            return None
        if upper == "OPOF":
            self.output_on = False
            self.model.set_output_enabled(False)
            return None
        if upper.startswith("VOLT "):
            try:
                self.voltage = float(line.split(None, 1)[1])
            except (IndexError, ValueError):
                logger.warning("SIM928: unparseable VOLT argument %r", line)
                return None
            self.model.set_bias_voltage(self.voltage)
            return None
        if upper in ("*RST", "*CLS"):
            self.voltage = 0.0
            self.output_on = False
            self.model.set_bias_voltage(0.0)
            self.model.set_output_enabled(False)
            return None

        logger.warning("SIM928: unrecognised command %r", line)
        return None


class Sim970(LineDevice):
    """A SIM970 four-channel voltmeter, one channel across the detector.

    The rest float, reading noise about zero. The reply format mirrors the real
    module's, a signed exponential-notation number, so the driver's parsing is
    exercised for real.
    """

    def __init__(self, model: SnspdModel, *, detector_channel: int = 1, num_channels: int = 4):
        self.model = model
        self.detector_channel = detector_channel
        self.num_channels = num_channels

    def handle(self, line: str) -> Optional[str]:
        upper = line.upper()

        if upper == "*IDN?":
            return SIM970_IDN
        if upper.startswith("VOLT?"):
            argument = line[len("VOLT?"):].strip()
            # Hardware channels are 1-based; anything outside 1..N means the
            # driver got its conversion wrong.
            try:
                channel = int(argument) if argument else 1
            except ValueError:
                logger.warning("SIM970: unparseable channel in %r", line)
                return None
            if not 1 <= channel <= self.num_channels:
                logger.warning("SIM970: channel %d out of range", channel)
                return None
            value = (
                self.model.device_voltage()
                if channel == self.detector_channel
                else self.model.floating_voltage()
            )
            return f"{value:+.6E}"

        logger.warning("SIM970: unrecognised command %r", line)
        return None


# --------------------------------------------------------------------------
# Mainframe and bus
# --------------------------------------------------------------------------


class Sim900(LineDevice):
    """A SIM900 mainframe: one GPIB address, several slots.

    Routing is the real thing: ``CONN <slot>, "<escape>"`` connects the GPIB
    port straight through to one module, every following line goes to that
    module verbatim, and the escape string disconnects. A driver that forgets
    to escape would therefore leave the mainframe connected and see its next
    mainframe-level command swallowed by a module — the same failure real
    hardware gives, which is worth reproducing.
    """

    def __init__(self, modules: dict[int, LineDevice]):
        self.modules = modules
        self._connected_slot: int | None = None
        self._escape = ""

    def handle(self, line: str) -> Optional[str]:
        if self._connected_slot is not None:
            if line == self._escape:
                self._connected_slot = None
                return None
            module = self.modules.get(self._connected_slot)
            if module is None:
                # An empty slot is not an error on a real mainframe; it simply
                # never answers.
                logger.debug("SIM900: command %r to empty slot %d", line, self._connected_slot)
                return None
            return module.handle(line)

        match = _CONN_RE.match(line)
        if match:
            self._connected_slot = int(match.group(1))
            self._escape = match.group(2)
            return None
        if line.upper() == "*IDN?":
            return SIM900_IDN

        logger.warning("SIM900: unrecognised mainframe command %r", line)
        return None


class GpibBus:
    """The devices reachable through one Prologix controller.

    Also keeps ``history`` — every ``(address, line)`` the drivers sent, in
    order. Tests assert on it to pin the wire protocol itself, which is the
    one thing a mocked instrument object can never check.
    """

    def __init__(self, devices: dict[int, LineDevice] | None = None):
        self.devices: dict[int, LineDevice] = dict(devices or {})
        self.history: list[tuple[int, str]] = []

    def send(self, address: int, line: str) -> Optional[str]:
        self.history.append((address, line))
        device = self.devices.get(address)
        if device is None:
            # Nothing at that address: no reply, exactly as on a real bus.
            logger.debug("GPIB bus: nothing listening at address %d", address)
            return None
        return device.handle(line)

    def commands_for(self, address: int) -> list[str]:
        return [line for addr, line in self.history if addr == address]


# --------------------------------------------------------------------------
# The controller
# --------------------------------------------------------------------------


class PrologixController:
    """A Prologix GPIB-USB controller, as the bytes it reads and writes.

    :meth:`feed` takes whatever arrived on the serial line and returns what the
    controller sends back. It splits the stream into lines, interprets the
    ``++`` controller commands itself, and forwards everything else to the
    addressed device on its :class:`GpibBus`. Replies are CR/LF-terminated
    lines, which is precisely how auto-read (``++auto 1``) presents them.
    """

    terminator = b"\r\n"

    def __init__(self, bus: GpibBus):
        self.bus = bus
        self._pending_input = b""
        # Controller state, mirroring what PrologixControllerDep configures.
        self.address = 0
        self.mode = 1
        self.auto = 1
        self.read_tmo_ms = 100

    def feed(self, data: bytes) -> bytes:
        self._pending_input += data
        # A single write may carry several lines (PrologixControllerDep sends
        # its whole configuration at once), and a line may arrive in pieces. A
        # trailing fragment is kept until its newline arrives.
        *lines, self._pending_input = self._pending_input.split(b"\n")
        out = bytearray()
        for raw in lines:
            reply = self._consume(raw.decode(errors="replace").strip("\r"))
            if reply is not None:
                out += reply.encode() + self.terminator
        return bytes(out)

    def _consume(self, line: str) -> Optional[str]:
        if not line:
            return None
        match = _CONTROLLER_RE.match(line)
        if match:
            return self._controller_command(match.group(1).lower(), match.group(2).strip())
        return self.bus.send(self.address, line)

    def _controller_command(self, name: str, argument: str) -> Optional[str]:
        if name == "addr":
            if not argument:
                # ``++addr`` with no argument is a query for the current address.
                return str(self.address)
            try:
                self.address = int(argument.split()[0])
            except ValueError:
                logger.warning("Prologix: bad ++addr argument %r", argument)
            return None
        if name in ("mode", "auto", "read_tmo_ms", "eos", "eoi", "eot_enable", "eot_char"):
            if argument:
                try:
                    setattr(self, name, int(argument.split()[0]))
                except ValueError:
                    logger.debug("Prologix: ignoring ++%s %r", name, argument)
            return None
        if name == "ver":
            return PROLOGIX_VERSION
        logger.debug("Prologix: unhandled controller command ++%s %s", name, argument)
        return None
