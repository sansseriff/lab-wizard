from lab_wizard.lib.instruments.general.vsource import VSource
from typing import Literal, Any
from pydantic import Field
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900ChildDep
from lab_wizard.lib.instruments.sim900.deps import Sim900Dep


class Sim928Params(SlotLike, ChildParams["Sim928"]):
    """Parameters for SIM928 voltage source module"""

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


class Sim928(Child[Sim900Dep, Sim928Params], VSource):
    """
    SIM928 module in the SIM900 mainframe
    Voltage source
    """

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @classmethod
    def from_params_with_dep(
        cls, parent_dep: Sim900Dep, key: str, params: ChildParams[Any]
    ) -> "Sim928":
        if not isinstance(params, Sim928Params):
            raise TypeError(
                f"Sim928.from_params_with_dep expected Sim928Params, got {type(params).__name__}"
            )
        dep = Sim900ChildDep(
            parent_dep.serial, parent_dep.gpibAddr, int(key), offline=params.offline
        )
        return cls(dep, params)

    def __init__(self, dep: Sim900ChildDep, params: Sim928Params):
        """
        :param comm: Communication object for this module
        :param params: Parameters for the module
        """
        self.dep = dep
        self.settling_time = params.settling_time
        self.attribute_name = params.attribute_name
        self.connected = True

    # Implement abstract VSource interface (single-channel instrument)
    def set_voltage(self, voltage: float) -> bool:  # type: ignore[override]
        apply_voltage = f"{voltage:0.3f}"
        result = self.dep.write(f"VOLT {apply_voltage}")
        return result is not None and result is not False

    def turn_on(self) -> bool:  # type: ignore[override]
        result = self.dep.write("OPON")
        return result is not None and result is not False

    def turn_off(self) -> bool:  # type: ignore[override]
        result = self.dep.write("OPOF")
        return result is not None and result is not False

    def disconnect(self) -> bool:  # type: ignore[override]
        self.connected = False
        return True
