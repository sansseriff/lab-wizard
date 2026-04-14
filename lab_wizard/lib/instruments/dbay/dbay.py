from typing import Any, Annotated, Literal
from pydantic import BaseModel, Field

from dbay import DBayClient

from lab_wizard.lib.instruments.general.parent_child import (
    Parent,
    ParentParams,
    ParentFactory,
    Child,
    CanInstantiate,
    IPLike,
    Discoverable,
)
from lab_wizard.lib.instruments.general.discovery import (
    ChildrenResult,
    DiscoveredChild,
    DiscoveryAction,
)
from lab_wizard.lib.instruments.dbay.modules.dac4d import Dac4DParams, Dac4D
from lab_wizard.lib.instruments.dbay.modules.dac16d import Dac16DParams, Dac16D
from lab_wizard.lib.instruments.dbay.modules.empty import EmptyParams, Empty


# Map DBay server module types to child Params types
_CHILD_TYPE_MAP: dict[str, str] = {"dac4D": "dac4D", "dac16D": "dac16D"}

DBayChildParams = Annotated[
    Dac4DParams | Dac16DParams | EmptyParams, Field(discriminator="type")
]


class DBayDiscoverChildrenParams(BaseModel):
    ip_address: str = Field("127.0.0.1", description="IP Address")
    ip_port: int = Field(8345, description="Port")


class DBayParams(
    IPLike,
    ParentParams["DBay", DBayClient, DBayChildParams],
    CanInstantiate["DBay"],
    Discoverable,
):
    """Params for DBay controller.

    GUI mode (default): ip_address + ip_port connect to the DBay GUI server.
    Direct UDP mode: ip_address + direct_port connect to hardware directly.
    Direct serial mode: serial_port + baudrate connect to hardware via serial.
    """

    type: Literal["dbay"] = "dbay"
    ip_address: str = "10.7.0.4"
    ip_port: int = 8345
    mode: Literal["gui", "direct"] = "gui"
    direct_port: int = Field(default=8880, description="UDP port for direct mode")
    direct_transport: Literal["udp", "serial"] = "udp"
    serial_port: str | None = None
    baudrate: int = 115200
    retain_changes: bool = Field(
        default=True, description="GUI mode: revert on cleanup if False"
    )
    children: dict[str, DBayChildParams] = Field(default_factory=dict)

    @property
    def inst(self):  # type: ignore[override]
        return DBay

    def create_inst(self) -> "DBay":
        return DBay.from_params(self)

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return [
            DiscoveryAction(
                name="populate_children",
                label="Discover & Sync Modules",
                description="Connect to DBay server and auto-populate child modules",
                params_model=DBayDiscoverChildrenParams,
                handler=cls._discover_children,
            ),
        ]

    @classmethod
    def _discover_children(
        cls, params: DBayDiscoverChildrenParams
    ) -> ChildrenResult:
        import json
        import urllib.request

        url = f"http://{params.ip_address}:{params.ip_port}/full-state"
        with urllib.request.urlopen(url, timeout=5) as r:
            state = json.loads(r.read())

        children: list[DiscoveredChild] = []
        for m in state.get("data", []):
            mtype = m.get("core", {}).get("type")
            slot = m.get("core", {}).get("slot")
            if mtype not in _CHILD_TYPE_MAP or slot is None:
                continue
            children.append(
                DiscoveredChild(
                    type=_CHILD_TYPE_MAP[mtype],
                    key_fields={"slot": str(slot)},
                )
            )
        return ChildrenResult(
            children=children,
            parent_key=f"{params.ip_address}:{params.ip_port}",
        )


class DBay(
    Parent[DBayClient, DBayChildParams],
    ParentFactory[DBayParams, "DBay"],
):
    """DBay controller - manages DAC modules via the dbay library.

    make_all_children and from_config are inherited from base classes.
    """

    def __init__(self, client: DBayClient, params: DBayParams):
        self.client = client
        self.params = params
        self.children: dict[str, Child[DBayClient, DBayChildParams]] = {}

    @property
    def dep(self) -> DBayClient:
        return self.client

    @classmethod
    def from_params(cls, params: DBayParams) -> "DBay":
        if params.mode == "gui":
            client = DBayClient(
                mode="gui",
                server_address=params.ip_address,
                port=params.ip_port,
                retain_changes=params.retain_changes,
            )
        elif params.direct_transport == "serial":
            client = DBayClient(
                mode="direct",
                direct_transport="serial",
                serial_port=params.serial_port,
                baudrate=params.baudrate,
            )
        else:
            client = DBayClient(
                mode="direct",
                direct_host=params.ip_address,
                direct_port=params.direct_port,
            )
        return cls(client, params)

    def make_child(self, key: str) -> Child[DBayClient, Any]:
        if key in self.children:
            return self.children[key]

        params = self.params.children[key]
        slot = int(params.slot)
        if self.params.mode == "gui":
            module = self.client.module(slot)
        else:
            from dbay import dac4D as dac4D_mod, dac16D as dac16D_mod

            if isinstance(params, Dac4DParams):
                module = self.client.attach_module(slot, dac4D_mod)
            elif isinstance(params, Dac16DParams):
                module = self.client.attach_module(slot, dac16D_mod)
            else:
                module = None
        child = params.inst(module, params)
        self.children[key] = child
        return child
