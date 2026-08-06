from typing import Literal, Any

from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.dbay.children import DBayModuleParams


class EmptyParams(SlotLike, DBayModuleParams):
    type: Literal["empty"] = "empty"
    name: str = "empty"

    @classmethod
    def resource_class(cls):
        return Empty


class Empty(Child[Any, EmptyParams]):
    def __init__(self, module: Any = None, params: EmptyParams | None = None):
        """Initialize an empty module."""
        self.module = module
        self.params = params or EmptyParams()

    def __str__(self) -> str:
        return "Empty slot"

    @property
    def dep(self) -> Any:
        return self.module
