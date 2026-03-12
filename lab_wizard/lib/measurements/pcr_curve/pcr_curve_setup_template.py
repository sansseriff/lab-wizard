"""
pcr_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass
from pathlib import Path
import yaml

from pydantic import BaseModel

from lab_wizard.lib.utilities.model_tree import Exp
from lab_wizard.lib.instruments.general.counter import Counter, StandInCounter
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter, StandInPlotter
from lab_wizard.lib.savers.saver import GenericSaver, StandInSaver

# wizard:imports:start
# wizard inserts concrete instrument imports here
# wizard:imports:end


class PCRCurveParams(BaseModel):
    bias_start_V: float = 0.0
    bias_end_V: float = 1.0
    bias_step_V: float = 0.01
    photon_rate: float = 100000.0


@dataclass
class PCRCurveResources:
    saver: GenericSaver
    plotter: GenericPlotter
    # wizard:resource_fields:start
    voltage_source: VSource
    counter: Counter
    # wizard:resource_fields:end
    params: PCRCurveParams


def load_exp_from_yaml(yaml_path: str | Path) -> Exp:
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Exp.model_validate(data)


def create_instrument_resources(exp: Exp) -> PCRCurveResources:
    saver_1 = StandInSaver()
    plotter_1 = StandInPlotter()
    voltage_source_1 = StandInVSource()
    counter_1 = StandInCounter()

    # wizard:instantiation:start
    # wizard inserts config-backed instrument construction here
    # wizard:instantiation:end

    return PCRCurveResources(
        saver=saver_1,
        plotter=plotter_1,
        # wizard:return_fields:start
        voltage_source=voltage_source_1,
        counter=counter_1,
        # wizard:return_fields:end
        params=PCRCurveParams(),
    )


if __name__ == "__main__":
    from lab_wizard.lib.measurements.pcr_curve.pcr_curve import PCRCurve

    this_file = Path(__file__).resolve()
    project_yaml = this_file.with_suffix(".yaml")
    exp = load_exp_from_yaml(project_yaml)
    resources = create_instrument_resources(exp)

    measurement = PCRCurve(params=resources.params, output_dir=Path("./data"))
    measurement.set_instruments(
        voltage_source=resources.voltage_source,
        voltmeter=resources.voltage_source,
        counter=resources.counter,
    )
