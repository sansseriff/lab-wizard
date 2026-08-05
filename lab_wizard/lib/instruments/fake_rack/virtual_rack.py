"""A rack of instruments that exists only as bytes on a fake serial port.

The point of these test doubles is that *nothing above the serial port knows
they are fake*. A measurement talks to a real :class:`Sim928`, which formats a
real ``VOLT 0.300``, which a real :class:`Sim900SlotDep` wraps in a real
``CONN 1, "esc"``, which a real :class:`PrologixControllerDep` prefixes with a
real ``++addr 5``. Only the last step — the pyserial handle — is replaced, by
:class:`FakePrologixSerial` here.

So the substitution happens at the lowest possible layer::

    Sim928 / Sim970          real driver, real SCPI strings
      Sim900SlotDep          real CONN/esc framing
      PrologixControllerDep  real ++addr, auto-read semantics
      FakePrologixSerial     <- the only fake part
      VirtualGpibBus         devices by GPIB address
      VirtualSim900          slot routing
      VirtualVoltageSource / VirtualVoltmeter
      SnspdModel             the physics

Everything the drivers send is *parsed*, not pattern-matched loosely: a
command this module does not recognise gets no reply and is recorded, so a
driver that starts sending malformed SCPI fails a test rather than quietly
reading a plausible number. That is the whole value of testing against a fake
device instead of a mocked method.

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

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel
from lab_wizard.lib.instruments.general.serial import SerialDep

logger = logging.getLogger("lab_wizard.lib.instruments.fake_rack.virtual_rack")

# ``CONN <slot>, "<escape>"`` — how a SIM900 is told to route the GPIB
# connection into one module until the escape string comes back.
_CONN_RE = re.compile(r'^CONN\s+(\d+)\s*,\s*"([^"]*)"$', re.IGNORECASE)
# Prologix controller commands are the ones starting with ``++``.
_CONTROLLER_RE = re.compile(r"^\+\+(\w+)\s*(.*)$")


class VirtualGpibDevice(ABC):
    """Something that answers on one GPIB address."""

    @abstractmethod
    def handle(self, line: str) -> Optional[str]:
        """Process one command line; return a reply, or ``None`` for silence."""


class VirtualSlotModule(ABC):
    """A module in a virtual mainframe slot."""

    @abstractmethod
    def handle(self, line: str) -> Optional[str]:
        """Process one command line; return a reply, or ``None`` for silence."""


# --------------------------------------------------------------------------
# Modules
# --------------------------------------------------------------------------


class VirtualVoltageSource(VirtualSlotModule):
    """Stands in for a SIM928 isolated voltage source.

    Understands the commands :class:`Sim928` actually sends (``VOLT``,
    ``OPON``, ``OPOF``) plus the queries a human would use at a terminal. Its
    output is not a number kept in a variable for its own sake — it is the
    bias applied to the shared :class:`SnspdModel`, which is what makes the
    voltmeter in another slot read something meaningful.
    """

    idn = "Lab_Wizard_Simulation,FAKE928,s/n000928,ver1.0"

    def __init__(self, model: SnspdModel):
        self.model = model
        self.voltage = 0.0
        self.output_on = False

    def handle(self, line: str) -> Optional[str]:
        upper = line.upper()

        if upper == "*IDN?":
            return self.idn
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
                logger.warning("FAKE928: unparseable VOLT argument %r", line)
                return None
            self.model.set_bias_voltage(self.voltage)
            return None
        if upper in ("*RST", "*CLS"):
            self.voltage = 0.0
            self.output_on = False
            self.model.set_bias_voltage(0.0)
            self.model.set_output_enabled(False)
            return None

        logger.warning("FAKE928: unrecognised command %r", line)
        return None


class VirtualVoltmeter(VirtualSlotModule):
    """Stands in for a SIM970 four-channel voltmeter.

    One channel is wired across the detector and reports what the
    :class:`SnspdModel` says; the rest float, reading noise about zero. Its
    reply format mirrors the real module's: a signed exponential-notation
    number, so the driver's ``float(...)`` parsing is exercised for real.
    """

    idn = "Lab_Wizard_Simulation,FAKE970,s/n000970,ver1.0"

    def __init__(self, model: SnspdModel, *, device_channel: int = 0, num_channels: int = 4):
        self.model = model
        self.device_channel = device_channel
        self.num_channels = num_channels

    def handle(self, line: str) -> Optional[str]:
        upper = line.upper()

        if upper == "*IDN?":
            return self.idn
        if upper.startswith("VOLT?"):
            argument = line[len("VOLT?"):].strip()
            # Hardware channels are 1-based; Sim970Channel converts before it
            # asks, so anything outside 1..N means the driver got it wrong.
            try:
                channel = int(argument) if argument else 1
            except ValueError:
                logger.warning("FAKE970: unparseable channel in %r", line)
                return None
            if not 1 <= channel <= self.num_channels:
                logger.warning("FAKE970: channel %d out of range", channel)
                return None
            index = channel - 1
            value = (
                self.model.device_voltage()
                if index == self.device_channel
                else self.model.floating_voltage()
            )
            return f"{value:+.6E}"

        logger.warning("FAKE970: unrecognised command %r", line)
        return None


# --------------------------------------------------------------------------
# Mainframe and bus
# --------------------------------------------------------------------------


class VirtualSim900(VirtualGpibDevice):
    """Stands in for a SIM900 mainframe: one GPIB address, several slots.

    Routing is the real thing: ``CONN <slot>, "<escape>"`` connects the GPIB
    port straight through to one module, every following line goes to that
    module verbatim, and the escape string disconnects. A driver that forgets
    to escape would therefore leave the mainframe connected and see its next
    mainframe-level command swallowed by a module — the same failure real
    hardware gives, which is worth reproducing.
    """

    idn = "Lab_Wizard_Simulation,FAKE900,s/n000900,ver1.0"

    def __init__(self, modules: dict[int, VirtualSlotModule], model: SnspdModel):
        self.modules = modules
        self.model = model
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
                logger.debug("FAKE900: command %r to empty slot %d", line, self._connected_slot)
                return None
            return module.handle(line)

        match = _CONN_RE.match(line)
        if match:
            self._connected_slot = int(match.group(1))
            self._escape = match.group(2)
            return None
        if line.upper() == "*IDN?":
            return self.idn

        logger.warning("FAKE900: unrecognised mainframe command %r", line)
        return None


class VirtualGpibBus:
    """The devices reachable through one fake Prologix controller.

    Also keeps ``history`` — every ``(address, line)`` the drivers sent, in
    order. Tests assert on it to pin the wire protocol itself, which is the
    one thing a mocked instrument object can never check.
    """

    def __init__(self, devices: dict[int, VirtualGpibDevice] | None = None):
        self.devices: dict[int, VirtualGpibDevice] = dict(devices or {})
        self.history: list[tuple[int, str]] = []

    def add(self, address: int, device: VirtualGpibDevice) -> None:
        self.devices[address] = device

    def send(self, address: int, line: str) -> Optional[str]:
        self.history.append((address, line))
        device = self.devices.get(address)
        if device is None:
            # Nothing at that address: no reply, exactly as on a real bus.
            logger.debug("Fake GPIB bus: nothing listening at address %d", address)
            return None
        return device.handle(line)

    def commands_for(self, address: int) -> list[str]:
        return [line for addr, line in self.history if addr == address]


# --------------------------------------------------------------------------
# The fake serial port
# --------------------------------------------------------------------------


class FakePrologixSerial(SerialDep):
    """A :class:`SerialDep` that answers as a Prologix GPIB-USB controller.

    This is the substitution point. It accepts the same byte stream pyserial
    would, splits it into lines, interprets the ``++`` controller commands
    itself, and forwards everything else to the addressed device on its
    :class:`VirtualGpibBus`. Replies are queued as CR/LF-terminated lines for
    :meth:`readline`, which is precisely how auto-read (``++auto 1``) presents
    them on a real controller.

    Reads never block: an empty queue returns ``b""``, the same as a pyserial
    read timeout. That means a driver bug that reads without having asked
    anything shows up as an empty-string parse failure rather than a hang.
    """

    terminator = b"\r\n"

    def __init__(self, bus: VirtualGpibBus, *, port: str = "sim://fake-rack"):
        self.bus = bus
        self.port = port
        self._open = True
        self._pending_input = b""
        self._pending_output = bytearray()
        # Controller state, mirroring what PrologixControllerDep configures.
        self.address = 0
        self.mode = 1
        self.auto = 1
        self.read_tmo_ms = 100

    # -- SerialDep interface --------------------------------------------------

    @property
    def is_open(self) -> bool:
        return self._open

    def write(self, data: bytes | str) -> int:
        if not self._open:
            raise RuntimeError(f"write to closed fake serial port {self.port}")
        payload = data.encode() if isinstance(data, str) else data
        self._pending_input += payload
        # A single write may carry several lines (PrologixControllerDep sends
        # its whole configuration at once, and every addressed command is
        # ``++addr N`` plus the command). A trailing fragment is kept until
        # its newline arrives.
        *lines, self._pending_input = self._pending_input.split(b"\n")
        for raw in lines:
            self._consume(raw.decode(errors="replace").strip("\r"))
        return len(payload)

    def read(self, size: Optional[int] = None) -> bytes:
        if size is None:
            out = bytes(self._pending_output)
            self._pending_output.clear()
            return out
        out = bytes(self._pending_output[:size])
        del self._pending_output[:size]
        return out

    def readline(self) -> bytes:
        index = self._pending_output.find(b"\n")
        if index == -1:
            # No reply waiting: a read timeout, not an error.
            return b""
        out = bytes(self._pending_output[: index + 1])
        del self._pending_output[: index + 1]
        return out

    def close(self) -> None:
        self._open = False

    # -- controller emulation -------------------------------------------------

    def _consume(self, line: str) -> None:
        if not line:
            return

        match = _CONTROLLER_RE.match(line)
        if match:
            self._controller_command(match.group(1).lower(), match.group(2).strip())
            return

        reply = self.bus.send(self.address, line)
        if reply is not None:
            self._reply(reply)

    def _controller_command(self, name: str, argument: str) -> None:
        if name == "addr":
            if argument:
                try:
                    self.address = int(argument.split()[0])
                except ValueError:
                    logger.warning("Fake Prologix: bad ++addr argument %r", argument)
                return
            # ``++addr`` with no argument is a query for the current address.
            self._reply(str(self.address))
            return
        if name in ("mode", "auto", "read_tmo_ms", "eos", "eoi", "eot_enable", "eot_char"):
            if argument:
                try:
                    setattr(self, name, int(argument.split()[0]))
                except (ValueError, AttributeError):
                    logger.debug("Fake Prologix: ignoring ++%s %r", name, argument)
            return
        if name == "read":
            # Explicit read: nothing is buffered on the device side here, so
            # this only matters for drivers that turn auto-read off.
            reply = self.bus.send(self.address, "*IDN?")
            if reply is not None:
                self._reply(reply)
            return
        if name == "ver":
            self._reply("Lab_Wizard_Simulation Prologix GPIB-USB Controller version 6.107")
            return

        logger.debug("Fake Prologix: unhandled controller command ++%s %s", name, argument)

    def _reply(self, text: str) -> None:
        self._pending_output += text.encode() + self.terminator
