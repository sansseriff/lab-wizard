from __future__ import annotations

from typing import Annotated, Any, Literal
import logging

logger = logging.getLogger(__name__)
from pydantic import Field, model_validator

from lab_wizard.lib.instruments.sim900.sim900 import Sim900, Sim900Params
from lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A import AndoAQ8201AParams
from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    ChildParams,
    CanInstantiate,
    USBLike,
    Discoverable,
)
from lab_wizard.lib.instruments.general.prologix_comm import PrologixControllerDep
from lab_wizard.lib.instruments.general.serial import SerialDep, LocalSerialDep
from lab_wizard.lib.utilities.model_tree import Exp


# Union of possible child param types on a serial bus (extend as needed)
PrologixChildParams = Annotated[
    Sim900Params | AndoAQ8201AParams, Field(discriminator="type")
]


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
    timeout: int = Field(
        default=1,
        description="(seconds)",
    )
    children: dict[str, PrologixChildParams] = Field(
        default_factory=dict,
    )

    @model_validator(mode="after")
    def _validate(self):
        return self

    @property
    def inst(self) -> type["PrologixGPIB"]:  # type: ignore[override]
        return PrologixGPIB

    def create_inst(self) -> "PrologixGPIB":
        return PrologixGPIB.from_params(self)

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "scan_usb",
                "label": "Scan USB Ports",
                "description": "Find Prologix GPIB-USB controllers connected to this computer",
                "inputs": [],
                "result_type": "probe",
            },
        ]

    @classmethod
    def run_discovery(
        cls, action: str, params: dict[str, Any], *, parent: Any = None
    ) -> dict[str, Any]:
        if action == "scan_usb":
            return cls._scan_usb()
        raise NotImplementedError(f"Unknown action: {action}")

    @classmethod
    def _scan_usb(cls) -> dict[str, Any]:
        import serial.tools.list_ports

        controllers = [
            {"port": p.device, "description": p.description}
            for p in serial.tools.list_ports.comports()
            if p.description and "Prologix" in p.description
        ]
        return {"found": controllers}


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
        serial_dep = LocalSerialDep(params.port, params.baudrate, float(params.timeout))
        controller = PrologixControllerDep(serial_dep)
        return cls(controller, params)

    def make_child(self, key: str) -> Child[Any, Any]:
        """Create a child instrument using its gpib_address param (not the hash key)."""
        if key in self.children:
            return self.children[key]
        child_params = self.params.children[key]
        gpib_dep = self._dep.addressed(int(child_params.gpib_address))
        child = child_params.inst(gpib_dep, child_params)  # type: ignore[arg-type]
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
