from __future__ import annotations

from typing import Annotated, TypeVar, cast, Any, Literal
from pydantic import Field, model_validator

from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params, Sim900
from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    ChildParams,
    CanInstantiate,
    USBLike,
)
from lab_wizard.lib.instruments.general.prologix_comm import PrologixControllerDep
from lab_wizard.lib.instruments.general.serial import SerialDep, LocalSerialDep
from lab_wizard.lib.instruments.sim900.comm import Sim900MainframeDep
from lab_wizard.lib.utilities.model_tree import Exp

# TypeVar for method-level inference
TChild = TypeVar("TChild", bound=Child[Any, Any])


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

    def __call__(self) -> "PrologixGPIB":
        return self.create_inst()


class PrologixGPIB(
    Parent[PrologixControllerDep, PrologixChildParams],
    ParentFactory[PrologixGPIBParams, "PrologixGPIB"],
):
    """
    PrologixGPIB implements the Parent + ParentFactory interfaces.
    Children receive progressively more specific comm objects built from the
    Prologix controller transport.
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

    @classmethod
    def from_config(cls, exp: Exp, key: str | int) -> "PrologixGPIB":
        norm_key = str(key)
        raw = exp.instruments[norm_key]
        if not isinstance(raw, PrologixGPIBParams):
            raise TypeError(
                f"Expected PrologixGPIBParams at exp.instruments[{norm_key!r}]"
            )
        return cls.from_params(raw)

    def disconnect(self):
        try:
            self.dep.close()
        except Exception:
            pass

    def _sim900_dep(self, key: str | int):
        return Sim900MainframeDep(self.dep.addressed(int(key)))

    def init_child_by_key(self, key: str) -> Child[Any, Any]:
        norm_key = str(key)
        if norm_key in self.children:
            return self.children[norm_key]

        child_params = self.params.children[norm_key]
        if not isinstance(child_params, Sim900Params):
            raise TypeError(
                f"Expected Sim900Params at Prologix child {norm_key!r}, got {type(child_params).__name__}"
            )
        child = Sim900(self._sim900_dep(norm_key), child_params)
        self.children[norm_key] = child
        return child

    def init_children(self) -> None:
        for key in list(self.params.children.keys()):
            self.init_child_by_key(key)

    def add_child(self, params: ChildParams[TChild], key: str) -> TChild:
        norm_key = str(key)
        self.params.children[norm_key] = params  # type: ignore[assignment]
        child = self.init_child_by_key(norm_key)
        return cast(TChild, child)

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
    sim900 = prologix.add_child(Sim900Params(), "3")
