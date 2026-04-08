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
from lab_wizard.lib.utilities.config_io import instrument_hash, save_instruments_to_config
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)

# Precomputed hash keys used throughout these tests
_PROLOGIX_KEY = instrument_hash("prologix_gpib", "/dev/ttyUSB0")
_SIM900_KEY = instrument_hash("sim900", "5")
_SIM928_KEY = instrument_hash("sim928", "1")
_SIM970_KEY = instrument_hash("sim970", "2")
_KEYSIGHT_KEY = instrument_hash("keysight53220A", "10.0.0.5:5025")


def _write_test_config(config_dir: Path) -> None:
    instruments = {
        _PROLOGIX_KEY: PrologixGPIBParams(
            port="/dev/ttyUSB0",
            children={
                _SIM900_KEY: Sim900Params(
                    gpib_address="5",
                    children={
                        _SIM928_KEY: Sim928Params(slot="1"),
                        _SIM970_KEY: Sim970Params(slot="2"),
                    },
                )
            },
        ),
        _KEYSIGHT_KEY: Keysight53220AParams(ip_address="10.0.0.5", ip_port=5025),
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
                key=_SIM928_KEY,
                path=[
                    SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                    SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                    SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                ],
            ),
            SelectedResource(
                variable_name="voltage_sense",
                type="sim970",
                key=_SIM970_KEY,
                channel_index=0,
                path=[
                    SelectedNodeRef(type="sim970", key=_SIM970_KEY),
                    SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                    SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
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
    assert _PROLOGIX_KEY in payload["instruments"]
    root = cast(dict[str, Any], payload["instruments"][_PROLOGIX_KEY])
    assert root["type"] == "prologix_gpib"
    assert _SIM900_KEY in root["children"]
    sim900 = cast(dict[str, Any], root["children"][_SIM900_KEY])
    assert sim900["type"] == "sim900"
    assert _SIM928_KEY in sim900["children"]
    assert _SIM970_KEY in sim900["children"]
    assert sim900["children"][_SIM928_KEY]["type"] == "sim928"
    assert sim900["children"][_SIM970_KEY]["type"] == "sim970"
    yaml_text = yaml_path.read_text(encoding="utf-8")
    assert "# (seconds)" in yaml_text

    setup_text = setup_path.read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928" in setup_text
    assert "from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970" in setup_text
    assert f"PrologixGPIB.from_config(exp, key={_PROLOGIX_KEY!r})" in setup_text
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
                key=_SIM928_KEY,
                path=[
                    SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                    # Wrong direct parent type on purpose; sim928 should be under sim900.
                    SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                ],
            ),
            SelectedResource(
                variable_name="voltage_sense",
                type="sim970",
                key=_SIM970_KEY,
                channel_index=0,
                path=[
                    SelectedNodeRef(type="sim970", key=_SIM970_KEY),
                    SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                    SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
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
                key=_SIM928_KEY,
                path=[
                    SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                    SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                    SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                ],
            ),
            SelectedResource(
                variable_name="counter",
                type="keysight53220A",
                key=_KEYSIGHT_KEY,
                channel_index=1,
                path=[SelectedNodeRef(type="keysight53220A", key=_KEYSIGHT_KEY)],
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
    assert f"PrologixGPIB.from_config(exp, key={_PROLOGIX_KEY!r})" in setup_text
    assert "Sim900.from_config(" in setup_text
    assert "Sim928.from_config(" in setup_text
    assert f"Keysight53220A.from_config(exp, key={_KEYSIGHT_KEY!r})" in setup_text
    assert ".add_child(" not in setup_text
    assert ".children[" not in setup_text
    assert "cast(" not in setup_text
    assert ".channels[1]" in setup_text
