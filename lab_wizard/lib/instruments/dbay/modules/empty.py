from typing import Literal, Any

from lab_wizard.lib.instruments.dbay.state import Core
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.dbay.comm import Comm


class EmptyParams(SlotLike, ChildParams["Empty"]):
    type: Literal["empty"] = "empty"
    name: str = "empty"

    @property
    def inst(self):  # type: ignore[override]
        return Empty


class Empty(Child[Comm, EmptyParams]):
    def __init__(self, data: dict[str, Any] | None = None):
        """Initialize an empty module."""
        if data:
            self.data = EmptyParams(**data)
            self.core = Core(
                slot=int(self.data.slot), type=self.data.type, name=self.data.name
            )

    def __str__(self):
        return "Empty slot"

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"
