from typing import Literal, Any, cast
from lab_wizard.lib.instruments.general.parent_child import Child, ChildParams, SlotLike
from lab_wizard.lib.instruments.sim900.comm import Sim900SlotDep


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


class Sim921(Child[Any, Sim921Params]):
    """
    SIM921 module in the SIM900 mainframe
    Resistance bridge
    """

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.sim900.sim900.Sim900"

    @classmethod
    def from_config(cls, parent: Any, key: str | int) -> "Sim921":
        norm_key = str(key)
        existing = getattr(parent, "children", {}).get(norm_key)
        if existing is not None:
            if not isinstance(existing, cls):
                raise TypeError(
                    f"Expected Sim921 child at {norm_key!r}, got {type(existing).__name__}"
                )
            return existing

        child_params = parent.params.children[norm_key]
        if not isinstance(child_params, Sim921Params):
            raise TypeError(
                f"Expected Sim921Params at {norm_key!r}, got {type(child_params).__name__}"
            )
        return cast("Sim921", parent.init_child_by_key(norm_key))

    def __init__(self, dep: Sim900SlotDep, params: Sim921Params):
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
