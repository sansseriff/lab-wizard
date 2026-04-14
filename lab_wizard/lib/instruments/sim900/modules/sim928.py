from lab_wizard.lib.instruments.general.vsource import VSource
from typing import Literal, Any
from pydantic import Field
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900SlotDep


class Sim928Params(SlotLike, ChildParams["Sim928"]):
    """Parameters for SIM928 voltage source module.

    ``slot`` (via SlotLike) holds the physical slot number within the SIM900
    mainframe and participates in hash key derivation.
    """

    type: Literal["sim928"] = "sim928"
    offline: bool | None = False
    settling_time: float | None = Field(
        default=0.4,
        description="(seconds)",
    )
    attribute_name: str | None = ""

    @property
    def inst(self):
        return Sim928


class Sim928(Child[Any, Sim928Params], VSource):
    """
    SIM928 module in the SIM900 mainframe
    Voltage source

    from_config is inherited from Child base class — no override needed.
    """

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    def __init__(self, dep: Sim900SlotDep, params: Sim928Params):
        self.dep = dep
        self.settling_time = params.settling_time
        self.attribute_name = params.attribute_name

    # Implement abstract VSource interface (single-channel instrument)
    def set_voltage(self, voltage: float) -> bool:  # type: ignore[override]
        apply_voltage = f"{voltage:0.3f}"
        result = self.dep.write(f"VOLT {apply_voltage}")
        return result is not False

    def turn_on(self) -> bool:  # type: ignore[override]
        result = self.dep.write("OPON")
        return result is not False

    def turn_off(self) -> bool:  # type: ignore[override]
        result = self.dep.write("OPOF")
        return result is not False
