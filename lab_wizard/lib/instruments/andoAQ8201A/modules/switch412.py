from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201ASlotDep


class Switch412Params(SlotLike, ChildParams["Switch412"]):
    type: Literal["ando_switch412"] = "ando_switch412"
    attribute_name: str = ""
    offline: bool = False

    @property
    def inst(self):
        return Switch412


class Switch412(Child[AndoAQ8201ASlotDep, Switch412Params]):
    """Ando AQ8201-412 Optical Switch Module."""

    def __init__(self, dep: AndoAQ8201ASlotDep, params: Switch412Params):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A.AndoAQ8201A"

    def set_switch(self, switch: str) -> None:
        """Select switch A or B."""
        switch = str(switch).upper()
        cmd = "D1" if switch == "A" else "D2"
        self._dep.write(cmd)

    def set_position(self, position: int) -> None:
        """Set switch position (1 or 2)."""
        cmd = "SA1SB1" if position == 1 else "SA1SB2"
        self._dep.write(cmd)
