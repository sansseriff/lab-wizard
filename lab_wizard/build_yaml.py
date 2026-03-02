from pathlib import Path

from pydantic_yaml import to_yaml_file  # type: ignore[import-untyped]

from lab_wizard.lib.utilities.model_tree import (
    Device,
    Exp,
    FileSaver,
    IVCurveParams,
    MplPlotter,
)

from lab_wizard.lib.instruments.dbay.dbay import DBayParams
from lab_wizard.lib.instruments.dbay.modules.dac4d import Dac4DParams

from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params

from ruamel.yaml import YAML


def create_example_exp() -> Exp:

    return Exp(
        exp=IVCurveParams(
            start_voltage=-2.0, stop_voltage=2.0, step_voltage=0.1, num_points=41
        ),
        device=Device(name="MyDevice", model="R1C3", description="My first device"),
        saver={
            "default": FileSaver(
                file_path="data/my_data.csv",
                include_timestamp=True,
                include_metadata=True,
            )
        },
        plotter={"default": MplPlotter(figure_size=[8, 6], dpi=100)},
        instruments={
            "10.7.0.88:8345": DBayParams(
                ip_address="10.7.0.88",
                ip_port=8345,
                children={"1": Dac4DParams()},
            ),
            "/dev/ttyUSB0": PrologixGPIBParams(
                port="/dev/ttyUSB0",
                baudrate=115200,
                children={
                    "5": Sim900Params(
                        children={
                            "1": Sim928Params(),
                            # SIM970 module in slot 5 with one active channel (channel 0)
                            "5": Sim970Params(),
                        }
                    )
                },
            ),
        },
    )


if __name__ == "__main__":

    # YAML writer configuration
    my_writer = YAML(
        typ="safe",
    )
    my_writer.default_flow_style = True
    my_writer.sort_base_mapping_type_on_output = False  # type: ignore[attr-defined]

    # Determine project and config locations
    repo_root = Path(__file__).resolve().parent.parent
    projects_dir = repo_root / "projects"
    project_dir = projects_dir / "demo_measurement_test_conf"
    project_dir.mkdir(parents=True, exist_ok=True)

    # 1) Create project-specific YAML that captures the in-memory Exp tree
    exp = create_example_exp()
    project_yaml = project_dir / "demo_measurement_test_conf.yaml"
    to_yaml_file(project_yaml, exp, custom_yaml_writer=my_writer)
    print(f"Wrote project-specific setup to {project_yaml}")

    # 2) Create a small helper script in the project folder that can push this
    #    project-specific instruments state into the global config tree.
    apply_script = project_dir / "apply_to_config.py"
    if not apply_script.exists():
        apply_script.write_text(
            """from pathlib import Path

from pydantic_yaml import parse_yaml_file_as

from lab_wizard.lib.utilities.model_tree import Exp
from lab_wizard.lib.utilities.config_io import load_merge_save_instruments


def main() -> None:
    this_file = Path(__file__).resolve()
    project_yaml = this_file.with_suffix(".yaml")

    # Repo root is two levels up: <repo_root>/projects/<project_name>/
    repo_root = this_file.parents[2]
    config_dir = repo_root / "lab_wizard" / "config"

    print(f\"Loading project-specific setup from: {project_yaml}\")
    exp = parse_yaml_file_as(Exp, project_yaml)

    # Push instruments subtree into the config/ instruments tree
    instruments = exp.instruments
    merged = load_merge_save_instruments(config_dir, instruments)
    print(f\"Pushed {len(instruments)} instrument(s) into config; merged total: {len(merged)}\")


if __name__ == \"__main__\":
    main()
"""
        )
        print(f"Created helper script to push config: {apply_script}")
    else:
        print(f"Helper script already exists: {apply_script}")
