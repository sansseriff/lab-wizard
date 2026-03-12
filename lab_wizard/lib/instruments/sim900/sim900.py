"""SIM900 mainframe and child-module construction."""

from typing import Annotated, Literal, cast, TypeVar, Any
from pydantic import Field


from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    Child,
    ChildParams,
)

from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970
from lab_wizard.lib.instruments.sim900.modules.sim921 import Sim921Params
from lab_wizard.lib.instruments.sim900.modules.sim921 import Sim921
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
    type: Literal["sim900"] = "sim900"

    @property
    def inst(self):
        return Sim900


TChild = TypeVar("TChild", bound=Child[Any, Any])


class Sim900(Parent[Sim900Dep, Sim900ChildParams], Child[Any, Any]):
    """
    SIM900 mainframe hybrid:
      - As Child of the Prologix controller: receives a GPIB-addressed mainframe comm
      - As Parent of SIM modules: supplies slot-addressed comm objects
    """

    def __init__(self, dep: Sim900Dep, params: Sim900Params):
        self.params = params
        self._dep = dep
        self.children: dict[str, Child[Any, Any]] = {}

    # Child interface requirement
    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"

    @classmethod
    def from_config(cls, parent: Any, key: str | int) -> "Sim900":
        norm_key = str(key)
        existing = getattr(parent, "children", {}).get(norm_key)
        if existing is not None:
            if not isinstance(existing, cls):
                raise TypeError(
                    f"Expected Sim900 child at {norm_key!r}, got {type(existing).__name__}"
                )
            return existing

        child_params = parent.params.children[norm_key]
        if not isinstance(child_params, Sim900Params):
            raise TypeError(
                f"Expected Sim900Params at {norm_key!r}, got {type(child_params).__name__}"
            )
        return cast("Sim900", parent.init_child_by_key(norm_key))

    # Parent abstract requirement
    @property
    def dep(self) -> Sim900Dep:
        return self._dep

    def init_child_by_key(self, key: str) -> Child[Any, Any]:
        norm_key = str(key)
        if norm_key in self.children:
            return self.children[norm_key]

        params = self.params.children[norm_key]
        slot_dep = self.dep.slot(int(norm_key), offline=bool(getattr(params, "offline", False)))
        if isinstance(params, Sim928Params):
            child: Child[Any, Any] = Sim928(slot_dep, params)
        elif isinstance(params, Sim970Params):
            child = Sim970(slot_dep, params)
        else:
            child = Sim921(slot_dep, params)
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


if __name__ == "__main__":
    print("yes")
