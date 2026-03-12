"""
iv_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass
from pathlib import Path
import yaml

from pydantic import BaseModel

from lab_wizard.lib.utilities.model_tree import Exp
from lab_wizard.lib.instruments.general.vsense import VSense, StandInVSense
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter, StandInPlotter
from lab_wizard.lib.savers.saver import GenericSaver, StandInSaver

# wizard:imports:start
# wizard inserts concrete instrument imports here
# wizard:imports:end


class IVCurveParams(BaseModel):
    start_V: float = 0.0
    end_V: float = 1.4
    step_V: float = 0.005
    bias_resistance: float = 100e3


@dataclass
class IVCurveResources:
    saver: GenericSaver
    plotter: GenericPlotter
    # wizard:resource_fields:start
    voltage_source: VSource
    voltage_sense: VSense
    # wizard:resource_fields:end
    params: IVCurveParams


def load_exp_from_yaml(yaml_path: str | Path) -> Exp:
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Exp.model_validate(data)


def create_instrument_resources(exp: Exp) -> IVCurveResources:
    saver_1 = StandInSaver()
    plotter_1 = StandInPlotter()
    voltage_source_1 = StandInVSource()
    voltage_sense_1 = StandInVSense()

    # wizard:instantiation:start
    # wizard inserts config-backed instrument construction here
    # wizard:instantiation:end

    return IVCurveResources(
        saver=saver_1,
        plotter=plotter_1,
        # wizard:return_fields:start
        voltage_source=voltage_source_1,
        voltage_sense=voltage_sense_1,
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
