from typing import Any, Annotated, Literal, cast
from pydantic import Field

from lab_wizard.lib.instruments.dbay.comm import Comm
from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    ChildParams,
    CanInstantiate,
    IPLike,
)
from lab_wizard.lib.instruments.dbay.modules.dac4d import Dac4DParams, Dac4D
from lab_wizard.lib.instruments.dbay.modules.dac16d import Dac16DParams, Dac16D
from lab_wizard.lib.instruments.dbay.modules.empty import EmptyParams, Empty
from lab_wizard.lib.utilities.model_tree import Exp


DBayChildParams = Annotated[
    Dac4DParams | Dac16DParams | EmptyParams, Field(discriminator="type")
]


class DBayParams(
    IPLike,
    ParentParams["DBay", Comm, DBayChildParams],
    CanInstantiate["DBay"],
):
    """Params for DBay controller.

    Instantiate via .create_inst() or calling the params object directly.
    Provide ip_address and ip_port for the DBay HTTP server.
    """

    type: Literal["dbay"] = "dbay"
    ip_address: str = "10.7.0.4"
    ip_port: int = 8345
    children: dict[str, DBayChildParams] = Field(default_factory=dict)

    @property
    def inst(self):  # type: ignore[override]
        return DBay

    def create_inst(self) -> "DBay":
        return DBay.from_params(self)

    def __call__(self) -> "DBay":
        return self.create_inst()


class DBay(
    Parent[Comm, DBayChildParams],
    ParentFactory[DBayParams, "DBay"],
):
    """DBay controller - manages DAC modules via HTTP communication.

    make_all_children, and from_config are inherited from base classes.
    """

    def __init__(self, dep: Comm, params: DBayParams):
        self.comm = dep
        self.params = params
        self.children: dict[str, Child[Comm, DBayChildParams]] = {}
        self._module_snapshot: list[Any] | None = None
        self._full_state_cache: dict[str, Any] | None = None

    @property
    def dep(self) -> Comm:
        return self.comm

    @classmethod
    def from_params(cls, params: "DBayParams") -> "DBay":
        return cls(Comm(params.ip_address, params.ip_port), params)

    def _full_state(self) -> dict[str, Any]:
        if self._full_state_cache is None:
            self._full_state_cache = self.comm.get("full-state")
        return self._full_state_cache

    def _module_info(self, slot: int) -> dict[str, Any]:
        data_list = self._full_state().get("data", [])
        return cast(dict[str, Any], data_list[slot])

    def make_child(self, key: str) -> Child[Comm, Any]:
        """Create a DAC module child using its slot param (not the hash key)."""
        if key in self.children:
            return self.children[key]

        params = self.params.children[key]
        # Use params.slot, NOT the hash key, to identify the hardware slot.
        slot = int(params.slot)
        module_info = self._module_info(slot)
        if isinstance(params, Dac4DParams):
            child = Dac4D.from_module_info(self.dep, slot, module_info, params)
        elif isinstance(params, Dac16DParams):
            child = Dac16D.from_module_info(self.dep, slot, module_info, params)
        else:
            child = Empty()
        self.children[key] = cast(Child[Comm, Any], child)
        return child

    def load_full_state(self) -> None:
        response = self._full_state()
        data: list[dict[str, dict[str, Any]]] = response.get("data", [])
        snapshot: list[Any] = []
        for module_info in data:
            t = module_info.get("core", {}).get("type")
            if t == "dac4D":
                core = module_info.setdefault("core", {})
                core.setdefault("slot", 0)
                core.setdefault("name", "dac4D-0")
                if "vsource" not in module_info:
                    module_info["vsource"] = {"channels": []}
                channels = module_info["vsource"].setdefault("channels", [])
                if not channels:
                    for i in range(4):
                        channels.append(
                            {
                                "index": i,
                                "bias_voltage": 0.0,
                                "activated": False,
                                "heading_text": f"CH{i}",
                                "measuring": False,
                            }
                        )
                snapshot.append(Dac4D(module_info, self.comm))
            elif t == "dac16D":
                snapshot.append(Dac16D(module_info, self.comm))
            else:
                snapshot.append(Empty())
        self._module_snapshot = snapshot

    def get_modules(self):
        if self._module_snapshot is None:
            self.load_full_state()
        return self._module_snapshot

    def list_modules(self):
        modules = self.get_modules() or []
        print("DBay Modules:")
        print("-------------")
        for i, module in enumerate(modules):
            print(f"Slot {i}: {module}")
        print("-------------")
        return modules
