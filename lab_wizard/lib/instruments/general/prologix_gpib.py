from __future__ import annotations

from typing import Annotated, TypeVar, cast, Any, Literal
from pydantic import Field, model_validator

from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    ChildParams,
    CanInstantiate,
    USBLike,
)
from lab_wizard.lib.instruments.general.serial import SerialDep, LocalSerialDep

# TypeVar for method-level inference
TChild = TypeVar("TChild", bound=Child[SerialDep, Any])


# Union of possible child param types on a serial bus (extend as needed)
PrologixChildParams = Annotated[Sim900Params, Field(discriminator="type")]


class PrologixGPIBParams(
    USBLike,
    ParentParams["PrologixGPIB", SerialDep, PrologixChildParams],
    CanInstantiate["PrologixGPIB"],
):
    """Params for Prologix GPIB controller.

    Instantiate via .create_inst() or calling the params object directly.
    `port` must be provided (defaults to /dev/ttyUSB0).
    """

    type: Literal["prologix_gpib"] = "prologix_gpib"
    baudrate: int = 9600
    timeout: int = 1
    children: dict[str, PrologixChildParams] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self):
        return self

    @property
    def inst(self) -> type["PrologixGPIB"]:  # type: ignore[override]
        return PrologixGPIB

    def create_inst(self) -> "PrologixGPIB":
        return PrologixGPIB.from_params(self)

    def __call__(self) -> "PrologixGPIB":
        return self.create_inst()


class PrologixGPIB(
    Parent[SerialDep, PrologixChildParams],
    ParentFactory[PrologixGPIBParams, "PrologixGPIB"],
):
    """
    PrologixGPIB implements the Parent + ParentFactory interfaces.
    Children (e.g., Sim900) receive the shared SerialDep serial connection.
    """

    def __init__(self, serial_dep: SerialDep, params: PrologixGPIBParams):
        self.params = params
        self._dep = serial_dep
        self.children: dict[str, Child[SerialDep, Any]] = {}

    @property
    def dep(self) -> SerialDep:
        return self._dep

    @classmethod
    def from_params(cls, params: PrologixGPIBParams) -> "PrologixGPIB":
        serial_dep = LocalSerialDep(params.port, params.baudrate, float(params.timeout))
        inst = cls(serial_dep, params)
        inst.init_children()
        return inst

    def disconnect(self):
        try:
            self._dep.close()
        except Exception:
            pass

    def init_child_by_key(self, key: str) -> Child[SerialDep, Any]:
        child_params = self.params.children[key]
        child_cls = child_params.inst
        child = child_cls.from_params_with_dep(self.dep, key, child_params)
        self.children[key] = child
        return child

    def init_children(self) -> None:
        for key in list(self.params.children.keys()):
            self.init_child_by_key(key)

    def add_child(self, params: ChildParams[TChild], key: str) -> TChild:
        self.params.children[key] = params  # type: ignore[assignment]
        child_cls = params.inst
        child = child_cls.from_params_with_dep(self.dep, key, params)
        self.children[key] = cast(Child[SerialDep, Any], child)
        return child

    def get_child(self, key: str) -> Child[SerialDep, Any] | None:
        return self.children.get(key)

    def list_children(self):
        print(f"Prologix Connection ({self.params.port}) Children:")
        print("=" * 50)
        for name, child in self.children.items():
            print(f"{name}: {child}")
        print("=" * 50)


if __name__ == "__main__":
    # Example top-level usage
    prologix = PrologixGPIBParams(port="/dev/ttyUSB0").create_inst()
    sim900 = prologix.add_child(Sim900Params(), "3")
