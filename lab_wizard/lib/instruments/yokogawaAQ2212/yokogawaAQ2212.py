from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from lab_wizard.lib.instruments.general.parent_child import (
    CanInstantiate,
    Child,
    IPLike,
    Parent,
    ParentFactory,
    ParentParams,
)
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212Dep, YokoAQ2212SlotDep
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.attenuator import AttenuatorParams
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.laser import LaserParams
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.power_meter import PowerMeterParams
from lab_wizard.lib.instruments.yokogawaAQ2212.modules.switch import SwitchParams

YokoAQ2212ChildParams = Annotated[
    LaserParams | AttenuatorParams | SwitchParams | PowerMeterParams,
    Field(discriminator="type"),
]


class YokogawaAQ2212Params(
    IPLike,
    ParentParams["YokogawaAQ2212", YokoAQ2212Dep, YokoAQ2212ChildParams],
    CanInstantiate["YokogawaAQ2212"],
):
    type: Literal["yokogawa_aq2212"] = "yokogawa_aq2212"
    ip_address: str = "10.7.0.13"
    ip_port: int = 50000
    offline: bool = False
    children: dict[str, YokoAQ2212ChildParams] = Field(default_factory=dict)

    @property
    def inst(self):
        return YokogawaAQ2212

    def create_inst(self) -> "YokogawaAQ2212":
        return YokogawaAQ2212.from_params(self)

    def __call__(self) -> "YokogawaAQ2212":
        return self.create_inst()


class YokogawaAQ2212(
    Parent[YokoAQ2212Dep, YokoAQ2212ChildParams],
    ParentFactory[YokogawaAQ2212Params, "YokogawaAQ2212"],
):
    def __init__(self, dep: YokoAQ2212Dep, params: YokogawaAQ2212Params):
        self.comm = dep
        self.params = params
        self.children: dict[str, Child[Any, Any]] = {}

    @property
    def dep(self) -> YokoAQ2212Dep:
        return self.comm

    @classmethod
    def from_params(cls, params: YokogawaAQ2212Params) -> "YokogawaAQ2212":
        return cls(YokoAQ2212Dep(params.ip_address, params.ip_port, offline=params.offline), params)

    def make_child(self, key: str) -> Child[Any, Any]:
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot_dep = self.dep.slot(int(params.slot))
        child = params.inst(slot_dep, params)  # type: ignore[arg-type]
        self.children[key] = child
        return child

    def set_date(self, year: int | None = None, month: int | None = None, day: int | None = None) -> None:
        from datetime import datetime
        if year is None:
            now = datetime.now()
            year, month, day = now.year, now.month, now.day
        self.comm.write(f"SYSTem:DATE {year},{month},{day}")

    def set_time(self, hour: int | None = None, minute: int | None = None, seconds: int | None = None) -> None:
        from datetime import datetime
        if hour is None:
            now = datetime.now()
            hour, minute, seconds = now.hour, now.minute, now.second
        self.comm.write(f"SYSTem:TIME {hour},{minute},{seconds}")
