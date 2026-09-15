"""
pcr_curve_setup_template.py

Template edited by the wizard during project generation.
The wizard only modifies blocks between matching
`# wizard:<name>:start` and `# wizard:<name>:end` markers.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from lab_procedure import Status

from lab_wizard.lib.client.claims import RoutedClaims
from lab_wizard.lib.client.project_resources import local_claims_for, resource_source_for
from lab_wizard.lib.measurements.pcr_curve.pcr_curve_params import PCRCurveParams
from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config
from lab_wizard.lib.task_adapters.lifecycle import RunLifecycle
from lab_wizard.lib.instruments.general.counter import Counter, StandInCounter
from lab_wizard.lib.instruments.general.vsource import VSource, StandInVSource
from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver

# wizard:imports:start
# wizard inserts concrete instrument / saver / plotter imports here
# wizard:imports:end


@dataclass
class PCRCurveResources:
    # wizard:resource_fields:start
    savers: list[GenericSaver] = field(default_factory=list)
    plotters: list[GenericPlotter] = field(default_factory=list)
    voltage_source: VSource = field(default_factory=StandInVSource)
    counter: Counter = field(default_factory=StandInCounter)
    # wizard:resource_fields:end
    params: PCRCurveParams = field(default_factory=PCRCurveParams)


def create_instrument_resources(
    project: ProjectConfig,
    resource_source: object | None = None,
) -> PCRCurveResources:
    resources = resource_source or project.resources
    # wizard:instantiation:start
    # wizard inserts config-backed instrument / saver / plotter construction here
    # wizard:instantiation:end

    return PCRCurveResources(
        # wizard:return_fields:start
        # wizard inserts the resolved field values here
        # wizard:return_fields:end
        params=PCRCurveParams.model_validate(project.measurement.params),
    )


if __name__ == "__main__":
    import argparse

    # The wizard copies the procedure beside this setup file so the generated
    # project is the editable, runnable source of truth.
    from pcr_curve import PCRCurveMeasurement

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

    # Local instruments resolve against this workspace's config/instruments;
    # routed ones through their servers (resources.instrument_sources). Only
    # the local racks this project uses are claimed. See project_resources.py.
    resource_source = resource_source_for(project, project_dir, remote=args.remote)
    claims = local_claims_for(project, project_dir, owner=project_dir.name, remote=args.remote)

    # Claim local transports, build the instruments, claim the ones reached
    # through a server, reset them to their configured baseline, run, make them
    # safe if the run fails, release. See lifecycle.py.
    status = RunLifecycle(
        claims=claims,
        claims_after_resolve=[
            lambda instruments: RoutedClaims(instruments, holder=project_dir.name)
        ],
    ).run(
        resolve=lambda: create_instrument_resources(project, resource_source),
        execute=lambda resources: PCRCurveMeasurement(resources).run_measurement(),
    )
    raise SystemExit(0 if status is Status.SUCCESS else 1)
