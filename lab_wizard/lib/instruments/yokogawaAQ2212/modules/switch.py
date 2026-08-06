from __future__ import annotations

from typing import Literal

from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.yokogawaAQ2212.children import YokogawaAQ2212ModuleParams
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212SlotDep


class SwitchParams(SlotLike, YokogawaAQ2212ModuleParams):
    type: Literal["yoko_switch"] = "yoko_switch"
    attribute_name: str = ""
    offline: bool = False

    @classmethod
    def resource_class(cls):
        return Switch


class Switch(Child[YokoAQ2212SlotDep, SwitchParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: SwitchParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    def get_status(self, dev: int = 1) -> str:
        return self._dep.query(f"ROUT{self.slot}:CHAN{dev}?")

    def set_position(self, position: int, dev: int) -> None:
        self._dep.write(f"ROUT{self.slot}:CHAN{dev} A,{position}")
