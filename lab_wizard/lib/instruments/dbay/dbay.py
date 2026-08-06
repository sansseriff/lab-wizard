import logging
from typing import Any, Literal
from pydantic import BaseModel, Field, SerializeAsAny

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
from lab_wizard.lib.instruments.dbay.children import DBayModuleParams


# Map DBay server module types (core.type in the GUI snapshot) to child Params
# types. Keys must match the server's casing exactly (dac4D / dac16D / adc4D) —
# no case sanitization. Any module type absent here is unsupported and is
# surfaced as a warning during discovery rather than silently dropped.
_CHILD_TYPE_MAP: dict[str, str] = {
    "dac4D": "dac4D",
    "dac16D": "dac16D",
    "adc4D": "adc4D",
}

DBayChildParams = DBayModuleParams


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
    children: dict[str, SerializeAsAny[DBayModuleParams]] = Field(default_factory=dict)

    @classmethod
    def resource_class(cls):
        return DBay

    def create_inst(self) -> "DBay":
        return DBay.from_params(self)

    # -- Transport ----------------------------------------------------------

    def transport_sharing(self):
        """GUI mode is shared; direct modes are not.

        In GUI mode the rack sits behind the DBay GUI backend, a lab-link
        reactive server that broadcasts state to every connected client — so
        several programs on different modules is the designed case and needs no
        arbitration from us. Direct serial owns a serial handle. Direct UDP has
        no connection to own, but two processes' replies cannot be attributed,
        so it is treated as exclusive until proven otherwise.
        """
        return "shared" if self.mode == "gui" else "exclusive"

    def state_authority(self):
        """GUI mode has its own authority; nothing else does.

        The GUI backend is authoritative and broadcasts changes, so a physicist
        moving a channel in the DBay GUI changes hardware we did not command.
        Inferring state from our own writes would leave the permission gate
        believing something false, so it must be read from the server instead.
        """
        return "subscribed" if self.mode == "gui" else "inferred"

    def transport_key(self) -> str | None:
        if self.mode == "gui":
            return f"dbay-gui://{self.ip_address}:{self.ip_port}"
        if self.direct_transport == "serial":
            return f"serial://{self.serial_port}" if self.serial_port else None
        return f"udp://{self.ip_address}:{self.direct_port}"

    def state_authority_client(self) -> DBayClient:
        """Client the server subscribes to for this rack's live state.

        Separate from the client used for commands: this one only reads and
        watches, so it is built with ``load_state=False`` (no module objects
        instantiated) and never mutates. ``retain_changes`` is irrelevant here
        for the same reason.
        """
        if self.mode != "gui":
            raise RuntimeError(
                "Only DBay in gui mode has an external state authority; "
                f"this instrument is in {self.mode!r} mode."
            )
        return DBayClient(
            mode="gui",
            server_address=self.ip_address,
            port=self.ip_port,
            load_state=False,
        )

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
        # Ask the DBay client which modules the GUI server reports. It owns the
        # transport (a lab-link websocket, formerly the HTTP /full-state
        # endpoint), so discovery never touches it directly. load_state=False
        # keeps construction cheap: present_modules() connects on demand and
        # does not instantiate live module objects.
        client = DBayClient(
            mode="gui",
            server_address=params.ip_address,
            port=params.ip_port,
            load_state=False,
        )
        try:
            present = client.present_modules()
        finally:
            client.close()

        children: list[DiscoveredChild] = []
        unsupported: list[str] = []
        for slot, mtype in present:
            if mtype not in _CHILD_TYPE_MAP:
                # Don't silently drop it — tell the user this build can't
                # configure the module so a missing module type is visible
                # instead of mysteriously absent from the tree.
                unsupported.append(f"slot {slot}: unsupported module type '{mtype}'")
                continue
            children.append(
                DiscoveredChild(
                    type=_CHILD_TYPE_MAP[mtype],
                    key_fields={"slot": str(slot)},
                )
            )
        if unsupported:
            logging.getLogger(__name__).warning(
                "DBay %s:%s reported %d unsupported module(s): %s",
                params.ip_address,
                params.ip_port,
                len(unsupported),
                "; ".join(unsupported),
            )
        return ChildrenResult(
            children=children,
            parent_key=f"{params.ip_address}:{params.ip_port}",
            warnings=unsupported,
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
        return self.instantiate_child(params, key=key)

    def instantiate_child(self, params: Any, *, key: str | None = None) -> Child[DBayClient, Any]:
        slot = int(params.slot)
        if self.params.mode == "gui":
            module = self.client.module(slot)
        else:
            from dbay import (
                dac4D as dac4D_mod,
                dac16D as dac16D_mod,
                ADC4D as adc4D_mod,
            )

            if params.type == "dac4D":
                module = self.client.attach_module(slot, dac4D_mod)
            elif params.type == "dac16D":
                module = self.client.attach_module(slot, dac16D_mod)
            elif params.type == "adc4D":
                module = self.client.attach_module(slot, adc4D_mod)
            else:
                module = None
        child = type(params).resource_class()(module, params)
        if key is not None:
            self.children[key] = child
        return child
