"""
iv_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from lab_wizard.lib.utilities.model_tree import Exp, load_exp_from_yaml
from lab_wizard.lib.instruments.general.vsense import VSense, StandInVSense
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver

# wizard:imports:start
# wizard inserts concrete instrument / saver / plotter imports here
# wizard:imports:end


class IVCurveParams(BaseModel):
    start_V: float = 0.0
    end_V: float = 1.4
    step_V: float = 0.005
    bias_resistance: float = 100e3


@dataclass
class IVCurveResources:
    # wizard:resource_fields:start
    savers: list[GenericSaver] = field(default_factory=list)
    plotters: list[GenericPlotter] = field(default_factory=list)
    voltage_source: VSource = field(default_factory=StandInVSource)
    voltage_sense: VSense = field(default_factory=StandInVSense)
    # wizard:resource_fields:end
    params: IVCurveParams = field(default_factory=IVCurveParams)


def create_instrument_resources(exp: Exp) -> IVCurveResources:
    # wizard:instantiation:start
    # wizard inserts config-backed instrument / saver / plotter construction here
    # wizard:instantiation:end

    return IVCurveResources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
        # wizard:return_fields:end
        params=IVCurveParams(),
    )


if __name__ == "__main__":
    from lab_wizard.lib.measurements.iv_curve.iv_curve import IVCurveMeasurement

    this_file = Path(__file__).resolve()
    project_yaml = this_file.with_suffix(".yaml")
    exp = load_exp_from_yaml(project_yaml)
    resources = create_instrument_resources(exp)
    measurement = IVCurveMeasurement(resources)
    measurement.run_measurement()
