from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from lab_wizard.lib.instruments.general.parent_child import (
    Child,
    ChildParams,
    GPIBAddressLike,
    Parent,
    ParentParams,
    Discoverable,
)
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201AFrameDep
from lab_wizard.lib.instruments.andoAQ8201A.modules.attenuator31 import Attenuator31Params
from lab_wizard.lib.instruments.andoAQ8201A.modules.switch412 import Switch412Params

AndoAQ8201AChildParams = Annotated[
    Attenuator31Params | Switch412Params,
    Field(discriminator="type"),
]


class AndoAQ8201AParams(
    GPIBAddressLike,
    ParentParams["AndoAQ8201A", AndoAQ8201AFrameDep, AndoAQ8201AChildParams],
    ChildParams["AndoAQ8201A"],
    Discoverable,
):
    """Parameters for Ando AQ8201A mainframe (hybrid Parent + Child).

    ``gpib_address`` (via GPIBAddressLike) holds the GPIB bus address used by
    the Prologix controller parent to scope the communication object.
    """

    type: Literal["ando_aq8201a"] = "ando_aq8201a"
    offline: bool = False
    children: dict[str, AndoAQ8201AChildParams] = Field(default_factory=dict)

    @property
    def inst(self):
        return AndoAQ8201A

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[dict[str, Any]]:
        return [
            {
                "name": "scan_gpib",
                "label": "Scan GPIB Bus",
                "description": "Search for Ando AQ8201A mainframes on a Prologix controller",
                "inputs": [],
                "parent_dep": "prologix_gpib",
                "result_type": "self_candidates",
            },
        ]

    @classmethod
    def run_discovery(cls, action: str, params: dict[str, Any], *, parent: Any = None) -> dict[str, Any]:
        if action == "scan_gpib":
            if parent is None:
                raise ValueError("scan_gpib requires a prologix_gpib parent")
            return cls._scan_gpib(parent)
        raise NotImplementedError(f"Unknown action: {action}")

    @classmethod
    def _scan_gpib(cls, parent_inst: Any) -> dict[str, Any]:
        from lab_wizard.lib.instruments.general.discovery import get_idn

        controller = parent_inst.dep

        found: list[dict[str, Any]] = []
        for address in range(30):
            idn = get_idn(controller, address)
            if not idn or not idn.startswith("ANDO"):
                continue
            found.append({
                "key_fields": {"gpib_address": str(address)},
                "idn": idn,
            })

        return {"found": found}


class AndoAQ8201A(
    Parent[AndoAQ8201AFrameDep, AndoAQ8201AChildParams],
    Child[Any, Any],
):
    """Ando AQ8201A mainframe hybrid:
      - As Child of PrologixGPIB: receives a raw PrologixAddressedInstrumentDep,
        wraps it into AndoAQ8201AFrameDep internally
      - As Parent of slot modules: supplies slot-scoped AndoAQ8201ASlotDeps
    """

    def __init__(self, dep: PrologixAddressedInstrumentDep, params: AndoAQ8201AParams):
        self.params = params
        self._dep = AndoAQ8201AFrameDep(dep)
        self.children: dict[str, Child[Any, Any]] = {}

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"

    @property
    def dep(self) -> AndoAQ8201AFrameDep:
        return self._dep

    def make_child(self, key: str) -> Child[Any, Any]:
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot_dep = self._dep.slot(int(params.slot), offline=bool(getattr(params, "offline", False)))
        child = params.inst(slot_dep, params)  # type: ignore[arg-type]
        self.children[key] = child
        return child
