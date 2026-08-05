"""
iv_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from lab_wizard.lib.client.composite_resources import CompositeResources
from lab_wizard.lib.client.preflight import preflight_local_project
from lab_wizard.lib.client.server_discovery import load_server_urls
from lab_wizard.lib.measurements.iv_curve.iv_curve_params import IVCurveParams
from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config
from lab_wizard.lib.instruments.general.vsense import VSense, StandInVSense
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver

# wizard:imports:start
# wizard inserts concrete instrument / saver / plotter imports here
# wizard:imports:end


@dataclass
class IVCurveResources:
    # wizard:resource_fields:start
    savers: list[GenericSaver] = field(default_factory=list)
    plotters: list[GenericPlotter] = field(default_factory=list)
    voltage_source: VSource = field(default_factory=StandInVSource)
    voltage_sense: VSense = field(default_factory=StandInVSense)
    # wizard:resource_fields:end
    params: IVCurveParams = field(default_factory=IVCurveParams)


def create_instrument_resources(
    project: ProjectConfig,
    resource_source: object | None = None,
) -> IVCurveResources:
    resources = resource_source or project.resources
    # wizard:instantiation:start
    # wizard inserts config-backed instrument / saver / plotter construction here
    # wizard:instantiation:end

    return IVCurveResources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
        # wizard:return_fields:end
        params=IVCurveParams.model_validate(project.measurement.params),
    )


if __name__ == "__main__":
    import argparse

    from lab_wizard.lib.measurements.iv_curve.iv_curve import IVCurveMeasurement

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--remote",
        default=None,
        help="Connect to a remote lab_wizard server (e.g. tcp://lab-server:12300). "
        "Requires this project's instruments to be named via attribute_name "
        "and the server to have a matching loaded project.",
    )
    args = parser.parse_args()

    # Each generated project directory contains exactly one project YAML named
    # after the directory; resolve it independently of this file's name.
    project_dir = Path(__file__).resolve().parent
    project_yaml = project_dir / f"{project_dir.name}.yaml"
    project = load_project_config(project_yaml)

    resource_source: object | None = None
    if args.remote:
        # Explicit override: route every *instrument* through one server. Savers
        # and plotters stay local — they write this machine's database and draw
        # on this machine's screen — which is why this is a composite rather
        # than a bare RemoteResources.
        resource_source = CompositeResources.all_remote(project, args.remote)
    elif project.resources.instrument_sources:
        # Per-attribute routing declared in the project YAML, so instruments may
        # be split between this machine and one or more servers.
        resource_source = CompositeResources.from_project(
            project, server_urls=load_server_urls(project_dir)
        )
    else:
        # Fully local: this process opens the instruments itself, so refuse if a
        # server on this machine already holds one of them. Naming the rack here
        # beats an opaque failure deep inside a driver.
        preflight_local_project(project.resources.instruments)

    resources = create_instrument_resources(project, resource_source)
    measurement = IVCurveMeasurement(resources)
    measurement.run_measurement()
