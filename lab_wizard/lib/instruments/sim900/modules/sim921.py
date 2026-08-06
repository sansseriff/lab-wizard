from typing import Literal, Any
from lab_wizard.lib.instruments.general.parent_child import Child, SlotLike
from lab_wizard.lib.instruments.sim900.children import Sim900ModuleParams
from lab_wizard.lib.instruments.sim900.comm import Sim900SlotDep


class Sim921Params(SlotLike, Sim900ModuleParams):
    """Parameters for SIM921 resistance bridge module.

    ``slot`` (via SlotLike) holds the physical slot number within the SIM900
    mainframe and participates in hash key derivation.
    """

    type: Literal["sim921"] = "sim921"
    num_channels: int = 1
    offline: bool | None = False
    settling_time: float | None = 0.1
    attribute_name: str | None = None

    @classmethod
    def resource_class(cls):
        return Sim921


class Sim921(Child[Any, Sim921Params]):
    """
    SIM921 module in the SIM900 mainframe
    Resistance bridge

    from_config is inherited from Child base class — no override needed.
    """

    def __init__(self, dep: Sim900SlotDep, params: Sim921Params):
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
