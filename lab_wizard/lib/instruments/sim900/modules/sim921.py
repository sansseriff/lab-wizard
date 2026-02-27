from typing import Literal, Any
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900ChildDep
from lab_wizard.lib.instruments.sim900.deps import Sim900Dep


class Sim921Params(SlotLike, ChildParams["Sim921"]):
    """Parameters for SIM921 resistance bridge module"""

    type: Literal["sim921"] = "sim921"
    slot: int
    num_channels: int = 1
    offline: bool | None = False
    settling_time: float | None = 0.1
    attribute_name: str | None = None

    @property
    def inst(self):
        return Sim921


class Sim921(Child[Sim900Dep, Sim921Params]):
    """
    SIM921 module in the SIM900 mainframe
    Resistance bridge
    """

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @classmethod
    def from_params_with_dep(
        cls, parent_dep: Sim900Dep, key: str, params: ChildParams[Any]
    ) -> "Sim921":
        if not isinstance(params, Sim921Params):
            raise TypeError(
                f"Sim921.from_params_with_dep expected Sim921Params, got {type(params).__name__}"
            )
        dep = Sim900ChildDep(
            parent_dep.serial, parent_dep.gpibAddr, int(key), offline=params.offline
        )
        return cls(dep, params)

    def __init__(self, dep: Sim900ChildDep, params: Sim921Params):
        """
        :param comm: Communication object for this module
        :param params: Parameters for the module
        """
        self.dep = dep
        self.settling_time = params.settling_time
        self.attribute_name = params.attribute_name
        self.slot = params.slot

    @property
    def mainframe_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    def getResistance(self) -> float:
        """
        gets the resistance from the bridge
        :return: the resistance in Ohm [float]
        """
        cmd = "RVAL?"
        res = self.dep.query(cmd)
        return float(res)
