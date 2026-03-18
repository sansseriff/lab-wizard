"""SIM900 mainframe and child-module construction."""

from typing import Annotated, Literal, Any
from pydantic import Field

from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    Child,
    ChildParams,
    GPIBAddressLike,
)
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.modules.sim921 import Sim921Params
from lab_wizard.lib.instruments.sim900.comm import Sim900MainframeDep
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep

Sim900ChildParams = Annotated[
    Sim928Params | Sim970Params | Sim921Params, Field(discriminator="type")
]


class Sim900Params(
    GPIBAddressLike,
    ParentParams["Sim900", Sim900MainframeDep, Sim900ChildParams],
    ChildParams["Sim900"],
):
    """Parameters for SIM900 mainframe (hybrid Parent + Child).

    ``gpib_address`` (via GPIBAddressLike) holds the GPIB bus address used by
    the Prologix controller parent to scope the communication object. It
    participates in hash derivation so the config tree key can be kept
    stable without exposing raw addresses in generated Python files.
    """

    children: dict[str, Sim900ChildParams] = Field(default_factory=dict)
    type: Literal["sim900"] = "sim900"

    @property
    def inst(self):
        return Sim900


class Sim900(Parent[Sim900MainframeDep, Sim900ChildParams], Child[Any, Any]):
    """
    SIM900 mainframe hybrid:
      - As Child of PrologixGPIB: receives a raw PrologixAddressedInstrumentDep,
        wraps it into Sim900MainframeDep internally
      - As Parent of SIM modules: supplies slot-scoped Sim900SlotDeps

    make_child, make_all_children, add_child, and from_config are all inherited
    from the base classes — only make_child needs a concrete implementation here.
    """

    def __init__(self, dep: PrologixAddressedInstrumentDep, params: Sim900Params):
        self.params = params
        self._dep = Sim900MainframeDep(dep)
        self.children: dict[str, Child[Any, Any]] = {}

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"

    @property
    def dep(self) -> Sim900MainframeDep:
        return self._dep

    def make_child(self, key: str) -> Child[Any, Any]:
        """Create a SIM module child using its slot param (not the hash key)."""
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot_dep = self._dep.slot(int(params.slot), offline=bool(getattr(params, "offline", False)))
        child = params.inst(slot_dep, params)  # type: ignore[arg-type]
        self.children[key] = child
        return child


if __name__ == "__main__":
    print("yes")
