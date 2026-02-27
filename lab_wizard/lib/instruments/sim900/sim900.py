"""
sim900.py
Author: Andrew Mueller, based on an older library by Alex Walter & Boris Korzh
Updated: July 10, 2025 by Andrew Mueller

A series of sub classes for the SRS sim900 mainframe
Includes:
  sim970 (voltmeter)
  sim928 (voltage source)
  sim921 (AC resistance bridge)
"""

from typing import Annotated, Literal, cast, TypeVar, Any
from pydantic import Field


from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    Child,
    ChildParams,
)

from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.modules.sim921 import Sim921Params
from lab_wizard.lib.instruments.general.serial import SerialDep
from lab_wizard.lib.instruments.sim900.deps import Sim900Dep

Sim900ChildParams = Annotated[
    Sim928Params | Sim970Params | Sim921Params, Field(discriminator="type")
]


class Sim900Params(
    ParentParams["Sim900", Sim900Dep, Sim900ChildParams], ChildParams["Sim900"]
):
    """Parameters for SIM900 mainframe (hybrid Parent + Child)."""

    # Parent-specific
    children: dict[str, Sim900ChildParams] = Field(default_factory=dict)
    num_children: int = 8
    type: Literal["sim900"] = "sim900"

    @property
    def inst(self):
        return Sim900


TChild = TypeVar("TChild", bound=Child[Sim900Dep, Any])


class Sim900(Parent[Sim900Dep, Sim900ChildParams], Child[SerialDep, Any]):
    """
    SIM900 mainframe hybrid:
      - As Child of SerialConnection: consumes SerialDep (shared SerialComm)
      - As Parent of SIM modules: supplies Sim900Dep (serial + GPIB address)
    """

    def __init__(self, dep: Sim900Dep, params: Sim900Params):
        # init params don't include keys. So, that means that for sim900 to be created,
        # keys (in this case GPIB number) must be present inside the dep that's supplied.
        # so __init__ must accept its corresponding dep that it uses internally (Sim900Dep). Not the
        # dep donated from the parent (SerialDep).
        self.params = params
        self._dep = dep
        self.children: dict[str, Child[Sim900Dep, Any]] = {}

    # Child interface requirement
    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"

    # Child interface factory expected by Parent implementations
    @classmethod
    def from_params_with_dep(
        cls,
        parent_dep: SerialDep,
        key: str,
        params: Sim900Params,
    ) -> "Sim900":
        sim_900_dep = Sim900Dep(parent_dep, int(key))

        return cls(sim_900_dep, params)

    # Parent abstract requirement
    @property
    def dep(self) -> Sim900Dep:
        return self._dep

    def init_child_by_key(self, key: str) -> Child[Sim900Dep, Any]:
        params = self.params.children[key]
        child_cls = params.inst
        # The specific param subtype matches child_cls, but the type checker
        # cannot express this dependency (params is a union). Cast to silence
        # the variance/union complaint.
        # child_typed = cast(type[Child[Sim900Dep, Sim900ChildParams]], child_cls)
        child = child_cls.from_params_with_dep(self.dep, key, params)
        self.children[key] = child
        return child

    def init_children(self) -> None:
        for key in list(self.params.children.keys()):
            self.init_child_by_key(key)

    def add_child(self, params: ChildParams[TChild], key: str) -> TChild:
        self.params.children[key] = params  # type: ignore[assignment]
        child_cls = params.inst
        child = child_cls.from_params_with_dep(self.dep, key, params)
        self.children[key] = cast(Child[Sim900Dep, Any], child)
        return child


if __name__ == "__main__":
    print("yes")
