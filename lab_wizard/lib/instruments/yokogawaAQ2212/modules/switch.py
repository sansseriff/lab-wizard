from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep


class SwitchParams(SlotLike, ChildParams["Switch"]):
    type: Literal["yoko_switch"] = "yoko_switch"
    attribute_name: str = ""
    offline: bool = False

    @property
    def inst(self):
        return Switch


class Switch(Child[YokoAQ2212SlotDep, SwitchParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: SwitchParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212.YokogawaAQ2212"

    def get_status(self, dev: int = 1) -> str:
        return self._dep.query(f"ROUT{self.slot}:CHAN{dev}?")

    def set_position(self, position: int, dev: int) -> None:
        self._dep.write(f"ROUT{self.slot}:CHAN{dev} A,{position}")
