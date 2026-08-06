"""FAKEGPIB — a Prologix GPIB controller whose serial port is imaginary.

This is the root of the simulated tree and the only class in the fake rack that
does anything unusual: instead of opening a serial port with pyserial, it
builds a :class:`FakePrologixSerial` over a
:class:`~lab_wizard.lib.instruments.fake_rack.virtual_rack.VirtualGpibBus`
populated from its own children. Above that line everything is the production
code path — the same :class:`PrologixControllerDep`, the same mainframe, the
same modules.

The rack topology comes from the config tree, so a project YAML that carries a
mainframe at GPIB 5 with a source in slot 1 and a voltmeter in slot 2 produces
a simulated rack with exactly those devices at exactly those addresses. Nothing
is auto-created: addressing a device the config does not declare goes
unanswered, which is what makes a mis-wired project fail in a test the same way
it would fail in the lab.

Sharing is declared as ``exclusive`` even though nothing physical is at stake.
That is deliberate: these instruments exist to let the layers above them be
tested honestly, and the transport-arbitration machinery (preflight checks,
single-owner rules) is one of those layers.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from pydantic import Field, SerializeAsAny

from lab_wizard.lib.instruments.fake_rack.children import FakeGpibChildParams
from lab_wizard.lib.instruments.fake_rack.virtual_rack import (
    FakePrologixSerial,
    VirtualGpibBus,
)
from lab_wizard.lib.instruments.general.discovery import (
    DiscoveryAction,
    NoParams,
    ProbeFound,
    ProbeResult,
)
from lab_wizard.lib.instruments.general.parent_child import (
    CanInstantiate,
    Discoverable,
    ParentParams,
    USBLike,
)
from lab_wizard.lib.instruments.general.prologix_comm import PrologixControllerDep
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIB
from lab_wizard.lib.instruments.general.transport import TransportSharing

logger = logging.getLogger("lab_wizard.lib.instruments.fake_rack.fakegpib")

# Default port for a simulated controller. It is not a device file, and is not
# meant to look like one: a config tree should say plainly which racks are real.
DEFAULT_FAKE_PORT = "sim://fake-rack-0"


class FakeGpibParams(
    USBLike,
    ParentParams["FakeGpib", PrologixControllerDep, FakeGpibChildParams],
    CanInstantiate["FakeGpib"],
    Discoverable,
):
    """Params for a simulated Prologix GPIB controller.

    ``baudrate`` and ``timeout`` are carried even though no UART is involved,
    so a simulated config and a real one have the same shape and the same
    generated code paths. ``timeout`` still reaches
    :class:`PrologixControllerDep`, which is what a driver's read-timeout
    handling is written against.
    """

    type: Literal["fakegpib"] = "fakegpib"
    port: str = DEFAULT_FAKE_PORT
    baudrate: int = 9600
    timeout: float = Field(
        default=0.15,
        description="(seconds) read timeout; simulated reads answer immediately",
    )
    children: dict[str, SerializeAsAny[FakeGpibChildParams]] = Field(default_factory=dict)

    @classmethod
    def resource_class(cls) -> type["FakeGpib"]:
        return FakeGpib

    def create_inst(self) -> "FakeGpib":
        return FakeGpib.from_params(self)

    # -- Transport ----------------------------------------------------------

    def transport_sharing(self) -> TransportSharing:
        return "exclusive"

    def transport_key(self) -> str | None:
        if not self.port:
            return None
        return self.port if "://" in self.port else f"sim://{self.port}"

    # -- Simulation ---------------------------------------------------------

    def virtual_bus(self) -> VirtualGpibBus:
        """Build the emulated bus this params subtree describes."""
        bus = VirtualGpibBus()
        for child in self.children.values():
            builder = getattr(child, "virtual_device", None)
            if builder is None:
                continue
            bus.add(int(child.gpib_address), builder())
        return bus

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return [
            DiscoveryAction(
                name="scan_usb",
                label="Offer a Simulated Controller",
                description="Simulated controllers are not attached to anything, so this simply offers one",
                params_model=NoParams,
                handler=cls._scan_usb,
            ),
        ]

    @classmethod
    def _scan_usb(cls, params: NoParams) -> ProbeResult:
        return ProbeResult(
            found=[
                ProbeFound(
                    port=DEFAULT_FAKE_PORT,
                    description="Simulated GPIB controller (no hardware)",
                )
            ]
        )


class FakeGpib(PrologixGPIB):
    """Prologix controller driver over a fake serial port.

    Only :meth:`from_params` differs from :class:`PrologixGPIB`: it substitutes
    the port. Child addressing, ``++addr`` scoping, and disconnection are the
    inherited production implementations.
    """

    @classmethod
    def from_params(cls, params: FakeGpibParams) -> "FakeGpib":  # type: ignore[override]
        serial_dep = FakePrologixSerial(params.virtual_bus(), port=params.port)
        controller = PrologixControllerDep(serial_dep, timeout_s=float(params.timeout))
        logger.info(
            "Opened simulated GPIB controller %s with %d device(s)",
            params.port,
            len(serial_dep.bus.devices),
        )
        return cls(controller, params)  # type: ignore[arg-type]

    @property
    def bus(self) -> VirtualGpibBus:
        """The emulated bus behind this controller, for tests to inspect."""
        serial_dep = self.dep.serial_dep
        assert isinstance(serial_dep, FakePrologixSerial)
        return serial_dep.bus
