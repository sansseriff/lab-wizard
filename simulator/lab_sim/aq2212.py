"""A Yokogawa AQ2212 frame with a variable optical attenuator in one slot.

The attenuator sits in the light path in front of the detector. Attenuating
scales the light that reaches it by ``10 ** (-dB / 10)``, and closing the
shutter (the module's *output*, in the AQ2212's terms) blocks it entirely. Dark
counts are unaffected by either, so a closed-shutter count measures the
background.

Like the hardware, it clamps to its maximum and quantizes to 0.001 dB. The
frame answers SCPI on a TCP port, where the lab's ``yokogawa_aq2212`` driver
finds it; every module command names its slot inline (``INP3:ATT 10``).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from lab_sim.snspd import SnspdModel

logger = logging.getLogger("lab_sim.aq2212")

AQ2212_IDN = "YOKOGAWA,AQ2212,SIMULATED,lab_sim"

_RESOLUTION_DB = 0.001
_MODULE_RE = re.compile(r"^(?P<subsystem>INP|OUTP)(?P<slot>\d+):(?P<tail>[A-Z]+)\s*(?P<argument>.*)$")


class Attenuator:
    """One attenuator module: attenuation, shutter and calibration wavelength."""

    def __init__(self, model: SnspdModel, *, max_attenuation_db: float = 60.0):
        self.model = model
        self.max_attenuation_db = max_attenuation_db
        self.attenuation_db = 0.0
        self.shutter_open = True
        self.wavelength_m = 1550e-9
        self._apply()

    def _apply(self) -> None:
        transmission = 10.0 ** (-self.attenuation_db / 10.0) if self.shutter_open else 0.0
        self.model.set_optical_transmission(transmission)

    def handle(self, subsystem: str, tail: str, argument: str) -> Optional[str]:
        query = argument.endswith("?") or tail.endswith("?")
        tail = tail.rstrip("?")
        if subsystem == "INP" and tail.startswith("ATT"):
            if query:
                return f"{self.attenuation_db:+.3f}"
            clamped = min(self.max_attenuation_db, max(0.0, float(argument)))
            self.attenuation_db = round(clamped / _RESOLUTION_DB) * _RESOLUTION_DB
            self._apply()
            return None
        if subsystem == "INP" and tail.startswith("WAV"):
            if query:
                return f"{self.wavelength_m:+.8E}"
            self.wavelength_m = float(argument)
            return None
        if subsystem == "OUTP" and tail.startswith("STAT"):
            if query:
                return "1" if self.shutter_open else "0"
            self.shutter_open = argument.strip().upper() in ("1", "ON")
            self._apply()
            return None
        raise ValueError(tail)


class AQ2212:
    """The frame: routes each module command to the slot it names."""

    def __init__(self, modules: dict[int, Attenuator]):
        self.modules = modules
        self.history: list[str] = []

    def handle(self, line: str) -> Optional[str]:
        self.history.append(line)
        upper = line.strip().upper()
        if upper == "*IDN?":
            return AQ2212_IDN
        if upper.startswith(("SYST:DATE", "SYSTEM:DATE", "SYST:TIME", "SYSTEM:TIME")):
            return None
        match = _MODULE_RE.match(upper)
        if match:
            module = self.modules.get(int(match.group("slot")))
            if module is None:
                logger.debug("AQ2212: command %r to empty slot", line)
                return None
            try:
                return module.handle(match.group("subsystem"), match.group("tail"), match.group("argument").strip())
            except ValueError:
                pass
        logger.warning("AQ2212: unrecognised command %r", line)
        return None
