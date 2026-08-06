"""SIM900 mainframe and child-module construction."""

from typing import Literal, Any
from pydantic import Field, SerializeAsAny

from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    Child,
    GPIBAddressLike,
    Discoverable,
)
from lab_wizard.lib.instruments.general.prologix_children import PrologixChildParams
from lab_wizard.lib.instruments.sim900.children import Sim900ModuleParams
from lab_wizard.lib.instruments.general.discovery import (
    DiscoveryAction,
    NoParams,
    SelfCandidate,
    SelfCandidatesResult,
)
from lab_wizard.lib.instruments.sim900.comm import Sim900MainframeDep
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep

Sim900ChildParams = Sim900ModuleParams


class Sim900Params(
    GPIBAddressLike,
    ParentParams["Sim900", Sim900MainframeDep, Sim900ChildParams],
    PrologixChildParams,
    Discoverable,
):
    """Parameters for SIM900 mainframe (hybrid Parent + Child).

    ``gpib_address`` (via GPIBAddressLike) holds the GPIB bus address used by
    the Prologix controller parent to scope the communication object. It
    participates in hash derivation so the config tree key can be kept
    stable without exposing raw addresses in generated Python files.
    """

    children: dict[str, SerializeAsAny[Sim900ModuleParams]] = Field(default_factory=dict)
    type: Literal["sim900"] = "sim900"

    @classmethod
    def resource_class(cls):
        return Sim900

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return [
            DiscoveryAction(
                name="scan_gpib",
                label="Scan GPIB Bus",
                description="Search for SIM900 mainframes on a Prologix controller",
                params_model=NoParams,
                handler=cls._scan_gpib,
                parent_dep="prologix_gpib",
            ),
        ]

    @classmethod
    def _scan_gpib(cls, params: NoParams, parent_inst: Any) -> SelfCandidatesResult:
        from lab_wizard.lib.instruments.general.discovery import get_idn

        controller = parent_inst.dep

        found: list[SelfCandidate] = []
        for address in range(30):
            idn = get_idn(controller, address)
            if not idn or not idn.startswith("Stanford_Research_Systems,SIM900"):
                continue
            found.append(
                SelfCandidate(
                    key_fields={"gpib_address": str(address)},
                    idn=idn,
                )
            )

        return SelfCandidatesResult(found=found)


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
    def dep(self) -> Sim900MainframeDep:
        return self._dep

    def make_child(self, key: str) -> Child[Any, Any]:
        """Create a SIM module child using its slot param (not the hash key)."""
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        return self.instantiate_child(params, key=key)

    def instantiate_child(self, params: Any, *, key: str | None = None) -> Child[Any, Any]:
        slot_dep = self._dep.slot(int(params.slot), offline=bool(getattr(params, "offline", False)))
        child = type(params).resource_class()(slot_dep, params)  # type: ignore[arg-type]
        if key is not None:
            self.children[key] = child
        return child


if __name__ == "__main__":
    print("yes")
