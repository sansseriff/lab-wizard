"""
pcr_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from lab_wizard.lib.utilities.model_tree import Exp, load_exp_from_yaml
from lab_wizard.lib.instruments.general.counter import Counter, StandInCounter
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver

# wizard:imports:start
# wizard inserts concrete instrument / saver / plotter imports here
# wizard:imports:end


class PCRCurveParams(BaseModel):
    bias_start_V: float = 0.0
    bias_end_V: float = 1.0
    bias_step_V: float = 0.01
    photon_rate: float = 100000.0


@dataclass
class PCRCurveResources:
    # wizard:resource_fields:start
    savers: list[GenericSaver] = field(default_factory=list)
    plotters: list[GenericPlotter] = field(default_factory=list)
    voltage_source: VSource = field(default_factory=StandInVSource)
    counter: Counter = field(default_factory=StandInCounter)
    # wizard:resource_fields:end
    params: PCRCurveParams = field(default_factory=PCRCurveParams)


def create_instrument_resources(exp: Exp) -> PCRCurveResources:
    # wizard:instantiation:start
    # wizard inserts config-backed instrument / saver / plotter construction here
    # wizard:instantiation:end

    return PCRCurveResources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
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
