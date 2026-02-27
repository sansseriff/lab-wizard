from typing import Any, Annotated, TypeVar, cast, Literal
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


# For now, restrict to Dac4D until other modules are migrated to the new generics.
DBayChildParams = Annotated[
    Dac4DParams | Dac16DParams | EmptyParams, Field(discriminator="type")
]


# TypeVar for method-level inference
TChild = TypeVar("TChild", bound=Child[Comm, Any])


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

    def __call__(self) -> "DBay":  # convenience
        return self.create_inst()


class DBay(
    Parent[Comm, DBayChildParams],
    ParentFactory[DBayParams, "DBay"],
):
    """DBay controller - manages DAC modules via HTTP communication."""

    def __init__(
        self, ip_address: str, ip_port: int = 8345, params: DBayParams | None = None
    ):
        self.ip_address = ip_address
        self.ip_port = ip_port
        self.comm = Comm(ip_address, ip_port)
        self.children: dict[str, Child[Comm, DBayChildParams]] = {}
        self._module_snapshot: list[Any] | None = None
        if params is not None:
            self.params = params

    @property
    def dep(self) -> Comm:
        return self.comm

    def init_child_by_key(self, key: str) -> Child[Comm, Any]:
        params = self.params.children[key]
        child_cls = params.inst
        child = child_cls.from_params_with_dep(self.dep, key, params)  # type: ignore[arg-type]
        self.children[key] = cast(Child[Comm, Any], child)
        return child

    def init_children(self) -> None:
        for key in list(getattr(self, "params", DBayParams()).children.keys()):
            self.init_child_by_key(key)

    def add_child(
        self,
        params: ChildParams[TChild],
        key: str,
    ) -> TChild:
        # Ensure params container exists
        if not hasattr(self, "params"):
            self.params = DBayParams(ip_address=self.ip_address, ip_port=self.ip_port)
        self.params.children[key] = params  # type: ignore[assignment]
        child_cls = params.inst
        child = child_cls.from_params_with_dep(self.dep, key, params)  # type: ignore[arg-type]
        self.children[key] = cast(Child[Comm, Any], child)
        return child

    # Back-compat helpers
    def load_full_state(self) -> None:
        response = self.comm.get("full-state")
        data: list[dict[str, dict[str, Any]]] = response.get("data", [])
        # Make a simple snapshot list so previous callers can list modules
        snapshot: list[Any] = []
        for module_info in data:
            t = module_info.get("core", {}).get("type")
            if t == "dac4D":
                # Normalize minimal test data to required structure
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

    @classmethod
    def from_params(cls, params: "DBayParams") -> "DBay":
        inst = cls(params.ip_address, params.ip_port, params)
        inst.init_children()
        return inst
