"""One simulated bench: a detector, and the instruments wired to it.

The bench is what a test setup in the lab would be — a voltage source and a
voltmeter in a SIM900 rack behind a Prologix controller, a counter on the
detector's output, and an attenuator in the light path — all on one SNSPD. It
is described by its own YAML file, so nothing about the detector ever appears
in lab_wizard's configuration::

    detector:
      critical_current_a: 3.0e-7
    prologix:
      link: ~/.lab_sim/prologix
      gpib_address: 5
    counter:
      port: 5025

Every field has a default; an empty file (or none) is the standard bench.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from lab_sim.aq2212 import AQ2212, Attenuator
from lab_sim.keysight53220a import Keysight53220A
from lab_sim.sim900 import GpibBus, PrologixController, Sim900, Sim928, Sim970
from lab_sim.snspd import SnspdModel, SnspdParams
from lab_sim.transports import Hub, PtySerial, TcpPort, line_feed

DEFAULT_LINK = "~/.lab_sim/prologix"


class PrologixConfig(BaseModel):
    """The SIM900 rack, reached through a Prologix GPIB-USB controller."""

    link: str = Field(default=DEFAULT_LINK, description="Stable path to the controller's serial port; use it as the port")
    gpib_address: int = Field(default=5, description="GPIB address of the SIM900 mainframe")
    source_slot: int = Field(default=1, description="SIM900 slot holding the SIM928 voltage source")
    voltmeter_slot: int = Field(default=2, description="SIM900 slot holding the SIM970 voltmeter")
    voltmeter_channel: int = Field(default=1, description="SIM970 input (1-4) wired across the detector")


class CounterConfig(BaseModel):
    """A Keysight 53220A counting the detector's pulses."""

    host: str = "127.0.0.1"
    port: int = Field(default=5025, description="TCP port; 0 picks a free one")


class AttenuatorConfig(BaseModel):
    """A Yokogawa AQ2212 with an attenuator module in the light path."""

    host: str = "127.0.0.1"
    port: int = Field(default=50000, description="TCP port; 0 picks a free one")
    slot: int = Field(default=1, description="AQ2212 slot holding the attenuator")
    max_attenuation_db: float = 60.0


class BenchConfig(BaseModel):
    detector: SnspdParams = Field(default_factory=SnspdParams)
    prologix: PrologixConfig = Field(default_factory=PrologixConfig)
    counter: CounterConfig = Field(default_factory=CounterConfig)
    attenuator: AttenuatorConfig = Field(default_factory=AttenuatorConfig)

    @classmethod
    def load(cls, path: Path | None) -> "BenchConfig":
        if path is None:
            return cls()
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


class Bench:
    """The bench's instruments on one detector, served until :meth:`stop`.

    Use it as a context manager, or call :meth:`start` and :meth:`stop`. The
    live objects are attributes, so a test can reach past the wire: ``model``
    for the physics, ``gpib.history`` for every command the rack was sent.
    """

    def __init__(self, config: BenchConfig | None = None):
        self.config = config or BenchConfig()
        c = self.config
        self.model = SnspdModel(c.detector)

        self.source = Sim928(self.model)
        self.voltmeter = Sim970(self.model, detector_channel=c.prologix.voltmeter_channel)
        self.gpib = GpibBus({
            c.prologix.gpib_address: Sim900({c.prologix.source_slot: self.source, c.prologix.voltmeter_slot: self.voltmeter}),
        })
        self.counter = Keysight53220A(self.model)
        self.attenuator = Attenuator(self.model, max_attenuation_db=c.attenuator.max_attenuation_db)
        self.aq2212 = AQ2212({c.attenuator.slot: self.attenuator})

        self._prologix = PtySerial(PrologixController(self.gpib).feed, Path(c.prologix.link).expanduser())
        self._counter = TcpPort(lambda: line_feed(self.counter.handle), c.counter.host, c.counter.port)
        self._aq2212 = TcpPort(lambda: line_feed(self.aq2212.handle), c.attenuator.host, c.attenuator.port)
        self._hub = Hub()
        self._hub.add_serial(self._prologix)
        self._hub.add_tcp(self._counter)
        self._hub.add_tcp(self._aq2212)

    # -- where to find it ------------------------------------------------------

    @property
    def prologix_port(self) -> str:
        """The serial port to give a ``prologix_gpib``."""
        return str(self._prologix.link)

    @property
    def counter_address(self) -> tuple[str, int]:
        return self._counter.host, self._counter.port

    @property
    def aq2212_address(self) -> tuple[str, int]:
        return self._aq2212.host, self._aq2212.port

    def describe(self) -> str:
        c = self.config
        return "\n".join([
            "Simulated bench running. Add these instruments in lab_wizard:",
            f"  prologix_gpib    port: {self.prologix_port}   (-> {self._prologix.device})",
            f"    sim900         gpib_address: {c.prologix.gpib_address}",
            f"      sim928       slot: {c.prologix.source_slot}",
            f"      sim970       slot: {c.prologix.voltmeter_slot}   (detector on its channel {c.prologix.voltmeter_channel}:"
            f" channel index {c.prologix.voltmeter_channel - 1} in lab_wizard)",
            f"  keysight53220A   ip_address: {self.counter_address[0]}  ip_port: {self.counter_address[1]}",
            f"  yokogawa_aq2212  ip_address: {self.aq2212_address[0]}  ip_port: {self.aq2212_address[1]}",
            f"    yoko_attenuator  slot: {c.attenuator.slot}",
        ])

    # -- lifetime --------------------------------------------------------------

    def start(self) -> "Bench":
        self._hub.start()
        return self

    def stop(self) -> None:
        self._hub.stop()

    def __enter__(self) -> "Bench":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
