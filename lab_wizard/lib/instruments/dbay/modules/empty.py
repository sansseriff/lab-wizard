from typing import Literal, Any

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike


class EmptyParams(SlotLike, ChildParams["Empty"]):
    type: Literal["empty"] = "empty"
    name: str = "empty"

    @property
    def inst(self):  # type: ignore[override]
        return Empty


class Empty(Child[Any, EmptyParams]):
    def __init__(self, module: Any = None, params: EmptyParams | None = None):
        """Initialize an empty module."""
        self.module = module
        self.params = params or EmptyParams()

    def __str__(self) -> str:
        return "Empty slot"

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.dbay.dbay.DBay"

    @property
    def dep(self) -> Any:
        return self.module
