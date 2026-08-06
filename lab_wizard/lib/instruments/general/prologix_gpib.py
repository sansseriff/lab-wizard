from __future__ import annotations

from typing import Any, Literal
import logging

logger = logging.getLogger(__name__)
from pydantic import Field, SerializeAsAny, model_validator

from lab_wizard.lib.instruments.general.prologix_children import PrologixChildParams
from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    CanInstantiate,
    USBLike,
    Discoverable,
)
from lab_wizard.lib.instruments.general.discovery import (
    DiscoveryAction,
    NoParams,
    ProbeFound,
    ProbeResult,
)
from lab_wizard.lib.instruments.general.prologix_comm import PrologixControllerDep
from lab_wizard.lib.instruments.general.serial import SerialDep, LocalSerialDep


class PrologixGPIBParams(
    USBLike,
    ParentParams["PrologixGPIB", SerialDep, PrologixChildParams],
    CanInstantiate["PrologixGPIB"],
    Discoverable,
):
    """Params for Prologix GPIB controller.

    Instantiate via .create_inst() or calling the params object directly.
    `port` must be provided (defaults to /dev/ttyUSB0).
    """

    type: Literal["prologix_gpib"] = "prologix_gpib"
    baudrate: int = 9600
    timeout: float = Field(
        default=0.15,
        description="(seconds) pyserial read timeout; also drives ++read_tmo_ms",
    )
    children: dict[str, SerializeAsAny[PrologixChildParams]] = Field(
        default_factory=dict,
    )

    @model_validator(mode="after")
    def _validate(self):
        return self

    @classmethod
    def resource_class(cls) -> type["PrologixGPIB"]:
        return PrologixGPIB

    def create_inst(self) -> "PrologixGPIB":
        return PrologixGPIB.from_params(self)

    # -- Transport ----------------------------------------------------------
    #
    # Exclusive for two compounding reasons. The serial port itself admits only
    # one holder (LocalSerialDep opens with exclusive=True). And even given a
    # shared byte stream the protocol could not be split: ``++addr N`` is
    # global controller state, a query is address-then-command, and under
    # ``++auto 1`` the reply carries no address — so two processes cannot tell
    # whose response they are reading. Serializing at the controller is what
    # the server is for; that is how many programs share one Prologix bus.

    def transport_key(self) -> str | None:
        return f"serial://{self.port}" if getattr(self, "port", None) else None

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return [
            DiscoveryAction(
                name="scan_usb",
                label="Scan USB Ports",
                description="Find Prologix GPIB-USB controllers connected to this computer",
                params_model=NoParams,
                handler=cls._scan_usb,
            ),
        ]

    @classmethod
    def _scan_usb(cls, params: NoParams) -> ProbeResult:
        import serial.tools.list_ports

        found = [
            ProbeFound(port=p.device, description=p.description)
            for p in serial.tools.list_ports.comports()
            if p.description and "Prologix" in p.description
        ]
        return ProbeResult(found=found)


class PrologixGPIB(
    Parent[PrologixControllerDep, PrologixChildParams],
    ParentFactory[PrologixGPIBParams, "PrologixGPIB"],
):
    """
    PrologixGPIB implements the Parent + ParentFactory interfaces.
    Children receive a GPIB-addressed comm object scoped to their gpib_address param.

    from_config and add_child/make_all_children are inherited from the base classes.
    """

    def __init__(self, controller: PrologixControllerDep, params: PrologixGPIBParams):
        self.params = params
        self._dep = controller
        self.children: dict[str, Child[Any, Any]] = {}

    @property
    def dep(self) -> PrologixControllerDep:
        return self._dep

    @classmethod
    def from_params(cls, params: PrologixGPIBParams) -> "PrologixGPIB":
        timeout = float(params.timeout)
        # pyserial timeout needs a small margin over the Prologix read timeout
        # so pyserial never returns while the Prologix is still mid-read.
        serial_dep = LocalSerialDep(params.port, params.baudrate, timeout + 0.05)
        controller = PrologixControllerDep(serial_dep, timeout_s=timeout)
        return cls(controller, params)

    def make_child(self, key: str) -> Child[Any, Any]:
        """Create a child instrument using its gpib_address param (not the hash key)."""
        if key in self.children:
            return self.children[key]
        child_params = self.params.children[key]
        return self.instantiate_child(child_params, key=key)

    def instantiate_child(self, child_params: Any, *, key: str | None = None) -> Child[Any, Any]:
        gpib_dep = self._dep.addressed(int(child_params.gpib_address))
        child = type(child_params).resource_class()(gpib_dep, child_params)  # type: ignore[arg-type]
        if key is not None:
            self.children[key] = child
        return child

    def disconnect(self):
        try:
            self.dep.close()
        except Exception:
            pass

    def get_child(self, key: str | int) -> Child[Any, Any] | None:
        return self.children.get(str(key))

    def list_children(self):
        print(f"Prologix Connection ({self.params.port}) Children:")
        print("=" * 50)
        for name, child in self.children.items():
            print(f"{name}: {child}")
        print("=" * 50)


if __name__ == "__main__":
    # Example top-level usage
    prologix = PrologixGPIBParams(port="/dev/ttyUSB0").create_inst()
    sim900 = Sim900(prologix.dep.addressed(5), Sim900Params())
