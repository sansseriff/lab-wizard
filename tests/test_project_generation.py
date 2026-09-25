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
from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    assign_missing_leaf_attribute_names,
    instrument_hash,
    save_instruments_to_config,
)
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)
from lab_wizard.wizard.backend.custom_resource_generation import (
    CustomResourceSelection,
    GenerateCustomResourceRequest,
    generate_custom_resource_project,
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
    # Mirror the wizard CRUD flow: config/instruments is always saved with
    # every hardware channel present and named.
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)


def test_generate_project_writes_sources_yaml_and_setup(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="iv_curve",
        kind="procedure",
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

    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
    )

    assert out["status"] == "ok"
    project_dir = Path(out["project_dir"])
    assert project_dir.exists()

    yaml_path = Path(out["yaml_file"])
    setup_path = Path(out["setup_file"])
    measurement_path = Path(out["measurement_file"])
    assert yaml_path.exists()
    assert setup_path.exists()
    assert measurement_path.exists()
    assert measurement_path.name == "iv_curve.py"
    assert "class IvCurveMeasurement" in measurement_path.read_text(encoding="utf-8")

    y = YAML(typ="safe")
    loader: Any = y
    payload = cast(dict[str, Any], loader.load(yaml_path.read_text(encoding="utf-8")))
    assert payload["project"]["measurement_type"] == "iv_curve"

    config = load_instruments(config_dir)
    sim900 = config[_PROLOGIX_KEY].children[_SIM900_KEY]
    source_name = sim900.children[_SIM928_KEY].attribute_name
    sense_name = sim900.children[_SIM970_KEY].channels[0].attribute_name
    # The project names its instruments and copies none of their params.
    assert "instruments" not in payload["resources"]
    assert payload["resources"]["instrument_sources"] == {source_name: "local", sense_name: "local"}

    setup_text = setup_path.read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "from iv_curve import IvCurveMeasurement" in setup_text
    assert f"voltage_source_1 = resources.from_attribute({source_name!r})" in setup_text
    assert f"voltage_sense_1 = resources.from_attribute({sense_name!r})" in setup_text
    assert ".from_config(resources, key=" not in setup_text
    assert "cast(" not in setup_text
    assert ".model_dump()" not in setup_text


def test_the_embedded_style_carries_the_selected_subset_with_comments(tmp_path: Path) -> None:
    """The escape hatch still writes the old self-contained shape."""
    config_dir = tmp_path / "config"
    _write_test_config(config_dir)
    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=tmp_path / "projects",
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            kind="procedure",
            generation_style="pedagogical_embedded",
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
        ),
    )
    yaml_path = Path(out["yaml_file"])
    payload = cast(dict[str, Any], YAML(typ="safe").load(yaml_path.read_text(encoding="utf-8")))
    root = cast(dict[str, Any], payload["resources"]["instruments"][_PROLOGIX_KEY])
    assert root["type"] == "prologix_gpib"
    sim900 = cast(dict[str, Any], root["children"][_SIM900_KEY])
    assert set(sim900["children"]) == {_SIM928_KEY, _SIM970_KEY}
    assert len(sim900["children"][_SIM970_KEY]["channels"]) == 1
    assert "# (seconds)" in yaml_path.read_text(encoding="utf-8")

    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "from_attribute" not in setup_text
    assert "model_validate(" in setup_text  # params embedded as Python


def test_save_instruments_writes_field_description_comments(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    _write_test_config(config_dir)

    inst_dir = config_dir / "instruments"
    sim970_files = list(inst_dir.rglob("sim970_key_*.yml"))
    assert sim970_files, "Expected a saved sim970 YAML file"
    sim970_text = sim970_files[0].read_text(encoding="utf-8")
    assert "settling_time:" in sim970_text
    assert "#" in sim970_text


def test_generate_project_rejects_wrong_parent_chain(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="iv_curve",
        kind="procedure",
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
        generate_procedure_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=req,
        )


def test_generate_pcr_project_references_the_selected_channel_by_name(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    req = GenerateProjectRequest(
        measurement_name="pcr_curve",
        kind="procedure",
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

    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=req,
    )

    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    y = YAML(typ="safe")
    loader: Any = y
    payload = cast(
        dict[str, Any],
        loader.load(Path(out["yaml_file"]).read_text(encoding="utf-8")),
    )
    config = load_instruments(config_dir)
    channel_name = config[_KEYSIGHT_KEY].channels[1].attribute_name
    assert channel_name
    # Channel 1's own name, so the project binds that input and no other.
    assert payload["resources"]["instrument_sources"][channel_name] == "local"
    assert "instruments" not in payload["resources"]
    ast.parse(setup_text)
    assert f"counter_1 = resources.from_attribute({channel_name!r})" in setup_text
    assert ".from_config(resources, key=" not in setup_text
    assert "cast(" not in setup_text


def test_custom_resources_refuse_the_retired_yaml_expanded_style(tmp_path: Path) -> None:
    """Retired here too, and for the same reason as in measurement creation.

    It taught hash traversal into a project's own instrument copy — which a
    custom resource no longer carries.
    """
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    with pytest.raises(ValueError, match="has been retired"):
        generate_custom_resource_project(
            config_dir=config_dir,
            projects_dir=projects_dir,
            req=GenerateCustomResourceRequest(
                generation_style="pedagogical_yaml_expanded",
                selections=[
                    CustomResourceSelection(
                        variable_name="bias_source",
                        type="sim928",
                        key=_SIM928_KEY,
                        path=[
                            SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                            SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                            SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                        ],
                    )
                ],
            ),
        )


def test_generate_custom_resource_pedagogical_embedded(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            generation_style="pedagogical_embedded",
            selections=[
                CustomResourceSelection(
                    variable_name="bias_source",
                    type="sim928",
                    key=_SIM928_KEY,
                    path=[
                        SelectedNodeRef(type="sim928", key=_SIM928_KEY),
                        SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                        SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                    ],
                )
            ],
        ),
    )

    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert "load_project_config" not in setup_text
    assert "PrologixGPIBParams.model_validate(" in setup_text
    assert "Sim900Params.model_validate(" in setup_text
    assert "Sim928Params.model_validate(" in setup_text
    assert ".from_config(" not in setup_text


def test_pedagogical_embedded_trims_selected_channel_payload(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    projects_dir = tmp_path / "projects"
    _write_test_config(config_dir)

    out = generate_custom_resource_project(
        config_dir=config_dir,
        projects_dir=projects_dir,
        req=GenerateCustomResourceRequest(
            generation_style="pedagogical_embedded",
            selections=[
                CustomResourceSelection(
                    variable_name="sense_ch1",
                    type="sim970",
                    key=_SIM970_KEY,
                    channel_index=1,
                    path=[
                        SelectedNodeRef(type="sim970", key=_SIM970_KEY),
                        SelectedNodeRef(type="sim900", key=_SIM900_KEY),
                        SelectedNodeRef(type="prologix_gpib", key=_PROLOGIX_KEY),
                    ],
                )
            ],
        ),
    )

    setup_text = Path(out["setup_file"]).read_text(encoding="utf-8")
    ast.parse(setup_text)
    assert ".channels[1]" in setup_text
    # Only the selected channel's params are embedded.
    assert setup_text.count("'settling_time': 0.1") == 1
