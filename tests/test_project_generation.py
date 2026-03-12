from pathlib import Path
from typing import Any, cast
import ast

import pytest
from ruamel.yaml import YAML

from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.keysight53220A import Keysight53220AParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.utilities.config_io import save_instruments_to_config
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)


def _write_test_config(config_dir: Path) -> None:
    instruments = {
        "/dev/ttyUSB0": PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                "5": Sim900Params(
                    children={
                        "1": Sim928Params(),
                        "2": Sim970Params(),
                    }
                )
            },
        ),
        "10.0.0.5:5025": Keysight53220AParams(ip_address="10.0.0.5", ip_port=5025),
    }
    save_instruments_to_config(instruments, config_dir)


def test_generate_project_creates_subset_yaml_and_setup(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="iv_curve",
        selected_resources=[
            SelectedResource(
                variable_name="voltage_source",
                type="sim928",
                key="1",
                path=[
                    SelectedNodeRef(type="sim928", key="1"),
                    SelectedNodeRef(type="sim900", key="5"),
                    SelectedNodeRef(type="prologix_gpib", key="/dev/ttyUSB0"),
                ],
            ),
            SelectedResource(
                variable_name="voltage_sense",
                type="sim970",
                key="2",
                channel_index=0,
                path=[
                    SelectedNodeRef(type="sim970", key="2"),
                    SelectedNodeRef(type="sim900", key="5"),
                    SelectedNodeRef(type="prologix_gpib", key="/dev/ttyUSB0"),
                ],
            ),
        ],
        project_prefix="iv_test",
    )

    out = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
    )

    assert out["status"] == "ok"
    project_dir = Path(out["project_dir"])
    assert project_dir.exists()

    yaml_path = Path(out["yaml_file"])
    setup_path = Path(out["setup_file"])
    assert yaml_path.exists()
    assert setup_path.exists()

    y = YAML(typ="safe")
    loader: Any = y
    payload = cast(dict[str, Any], loader.load(yaml_path.read_text(encoding="utf-8")))
    assert "/dev/ttyUSB0" in payload["instruments"]
    root = cast(dict[str, Any], payload["instruments"]["/dev/ttyUSB0"])
    assert root["type"] == "prologix_gpib"
    assert "5" in root["children"]
    sim900 = cast(dict[str, Any], root["children"]["5"])
    assert sim900["type"] == "sim900"
    assert "1" in sim900["children"]
    assert "2" in sim900["children"]
    assert sim900["children"]["1"]["type"] == "sim928"
    assert sim900["children"]["2"]["type"] == "sim970"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "# (seconds)" in yaml_text

    setup_text = setup_path.read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928" in setup_text
    assert "from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970" in setup_text
    assert "PrologixGPIB.from_config(exp, '/dev/ttyUSB0')" in setup_text
    assert "Sim900.from_config(" in setup_text
    assert "Sim928.from_config(" in setup_text
    assert "Sim970.from_config(" in setup_text
    assert ".add_child(" not in setup_text
    assert ".children[" not in setup_text
    assert "cast(" not in setup_text
    assert ".model_dump()" not in setup_text
    assert "voltage_source_1 = " in setup_text
    assert "voltage_sense_1 = " in setup_text
    assert ".channels[0]" in setup_text


def test_save_instruments_writes_field_description_comments(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_test_config(config_dir)

    inst_dir = config_dir / "instruments"
    sim928_files = list(inst_dir.rglob("sim928_key_*.yml"))
    assert sim928_files, "Expected a saved sim928 YAML file"
    sim928_text = sim928_files[0].read_text(encoding="utf-8")
    assert "settling_time:" in sim928_text
    assert "#" in sim928_text


def test_generate_project_rejects_wrong_parent_chain(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="iv_curve",
        selected_resources=[
            SelectedResource(
                variable_name="voltage_source",
                type="sim928",
                key="1",
                path=[
                    SelectedNodeRef(type="sim928", key="1"),
                    # Wrong direct parent type on purpose; sim928 should be under sim900.
                    SelectedNodeRef(type="prologix_gpib", key="/dev/ttyUSB0"),
                ],
            ),
            SelectedResource(
                variable_name="voltage_sense",
                type="sim970",
                key="2",
                channel_index=0,
                path=[
                    SelectedNodeRef(type="sim970", key="2"),
                    SelectedNodeRef(type="sim900", key="5"),
                    SelectedNodeRef(type="prologix_gpib", key="/dev/ttyUSB0"),
                ],
            ),
        ],
    )

    with pytest.raises(ValueError):
        generate_measurement_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=req,
        )


def test_generate_pcr_project_uses_from_config_style(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="pcr_curve",
        selected_resources=[
            SelectedResource(
                variable_name="voltage_source",
                type="sim928",
                key="1",
                path=[
                    SelectedNodeRef(type="sim928", key="1"),
                    SelectedNodeRef(type="sim900", key="5"),
                    SelectedNodeRef(type="prologix_gpib", key="/dev/ttyUSB0"),
                ],
            ),
            SelectedResource(
                variable_name="counter",
                type="keysight53220A",
                key="10.0.0.5:5025",
                channel_index=1,
                path=[SelectedNodeRef(type="keysight53220A", key="10.0.0.5:5025")],
            ),
        ],
        project_prefix="pcr_test",
    )

    out = generate_measurement_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
    )

    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "PrologixGPIB.from_config(exp, '/dev/ttyUSB0')" in setup_text
    assert "Sim900.from_config(" in setup_text
    assert "Sim928.from_config(" in setup_text
    assert "Keysight53220A.from_config(exp, '10.0.0.5:5025')" in setup_text
    assert ".add_child(" not in setup_text
    assert ".children[" not in setup_text
    assert "cast(" not in setup_text
    assert ".channels[1]" in setup_text

