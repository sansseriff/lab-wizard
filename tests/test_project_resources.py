"""How a generated project finds its instruments when it runs.

Since instrument params left the project YAML (procedure plan 5.4), a project
names each instrument and resolves it against its workspace's config — or, for
an older or embedded-style project, against its own copy, exactly as before.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from lab_wizard.lib.client import local_claims
from lab_wizard.lib.client.composite_resources import CompositeResources
from lab_wizard.lib.client.project_resources import (
    WorkspaceNotFound,
    local_claims_for,
    resource_source_for,
    uses_workspace_tree,
)
from lab_wizard.lib.instruments.fake_rack.fake900 import Fake900Params
from lab_wizard.lib.instruments.fake_rack.fake_counter import FakeCounterParams
from lab_wizard.lib.instruments.fake_rack.fakegpib import FakeGpibParams
from lab_wizard.lib.instruments.fake_rack.modules.fake928 import Fake928Params
from lab_wizard.lib.instruments.fake_rack.modules.fake970 import Fake970Params
from lab_wizard.lib.instruments.fake_rack.wiring import reset_detectors
from lab_wizard.lib.utilities.config_io import (
    assign_missing_leaf_attribute_names,
    instrument_hash,
    load_instruments,
    save_instruments_to_config,
)
from lab_wizard.lib.utilities.model_tree import ResourceConfig, load_project_config
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import (
    GenerateProjectRequest,
    SelectedNodeRef,
    SelectedResource,
    generate_measurement_project,
)

RACK = instrument_hash("fakegpib", "sim://resources-rack")
MAINFRAME = instrument_hash("fake900", "5")
SOURCE = instrument_hash("fake928", "1")
METER = instrument_hash("fake970", "2")
COUNTER = instrument_hash("fake_counter", "sim://resources-counter:5025")


@pytest.fixture(autouse=True)
def _cold_lab() -> None:
    reset_detectors()


@pytest.fixture
def no_servers(monkeypatch):
    monkeypatch.setattr(local_claims, "preflight_local_project", lambda _instruments: None)


def _workspace(root: Path) -> Path:
    config_dir = root / "config"
    instruments: dict[str, Any] = {
        RACK: FakeGpibParams(
            port="sim://resources-rack",
            children={
                MAINFRAME: Fake900Params(
                    gpib_address="5",
                    detector_name="resources",
                    children={SOURCE: Fake928Params(slot="1"), METER: Fake970Params(slot="2")},
                )
            },
        ),
        COUNTER: FakeCounterParams(ip_address="sim://resources-counter", detector_name="resources"),
    }
    assign_missing_leaf_attribute_names(instruments)
    save_instruments_to_config(instruments, config_dir)
    return config_dir


def _iv_project(root: Path, style: str = "production") -> Path:
    config_dir = _workspace(root)
    rack = [SelectedNodeRef(type="fake900", key=MAINFRAME), SelectedNodeRef(type="fakegpib", key=RACK)]
    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=root / "projects",
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            kind="procedure",
            generation_style=style,
            selected_resources=[
                SelectedResource(variable_name="voltage_source", type="fake928", key=SOURCE,
                                 path=[SelectedNodeRef(type="fake928", key=SOURCE), *rack]),
                SelectedResource(variable_name="voltage_sense", type="fake970", key=METER, channel_index=0,
                                 path=[SelectedNodeRef(type="fake970", key=METER), *rack]),
            ],
        ),
    )
    return Path(out["project_dir"])


def _names(config_dir: Path) -> tuple[str, str]:
    config = load_instruments(config_dir)
    mainframe = config[RACK].children[MAINFRAME]
    return mainframe.children[SOURCE].attribute_name, mainframe.children[METER].channels[0].attribute_name


# --------------------------- resolution ---------------------------


def test_a_project_resolves_its_instruments_from_the_workspace(tmp_path: Path):
    project_dir = _iv_project(tmp_path)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    assert uses_workspace_tree(project)

    source = resource_source_for(project, project_dir)
    assert isinstance(source, CompositeResources)
    source_name, _meter = _names(tmp_path / "config")
    assert source.from_attribute(source_name) is not None


def test_two_instruments_in_one_rack_share_the_rack(tmp_path: Path, monkeypatch):
    """Built twice, a rack would open its serial port twice — and a simulated
    rack would split one detector into two that disagree."""
    config_dir = _workspace(tmp_path)
    built: list[str] = []
    original = FakeGpibParams.create_inst
    monkeypatch.setattr(FakeGpibParams, "create_inst", lambda self: built.append(self.port) or original(self))

    resources = ResourceConfig(instruments=load_instruments(config_dir))
    source_name, meter_name = _names(config_dir)
    resources.from_attribute(source_name)
    resources.from_attribute(meter_name)

    assert built == ["sim://resources-rack"]


def test_a_bench_change_in_the_workspace_reaches_the_project_without_regenerating(tmp_path: Path):
    """The point of not copying params: edit the workspace once, not every project."""
    project_dir = _iv_project(tmp_path)
    config_dir = tmp_path / "config"
    config = load_instruments(config_dir)
    config[RACK].children[MAINFRAME].children[METER].channels[0].settling_time = 0.25
    save_instruments_to_config(config, config_dir)

    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    _source, meter_name = _names(config_dir)
    meter = resource_source_for(project, project_dir).from_attribute(meter_name)
    assert meter.settling_time == 0.25


def test_outside_a_workspace_the_error_says_what_to_do(tmp_path: Path):
    project_dir = _iv_project(tmp_path / "lab")
    moved = tmp_path / "elsewhere" / project_dir.name
    shutil.copytree(project_dir, moved)
    project = load_project_config(moved / f"{moved.name}.yaml")

    with pytest.raises(WorkspaceNotFound, match="embedded style to make it self-contained"):
        resource_source_for(project, moved)


# --------------------------- claims ---------------------------


def test_only_the_racks_a_project_uses_are_claimed(tmp_path: Path, no_servers):
    project_dir = _iv_project(tmp_path)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")

    claims = local_claims_for(project, project_dir, owner="iv")
    assert len(claims) == 1
    # The rack both instruments live in — not the counter, which is also in
    # the workspace but not in this project.
    assert set(claims[0].instruments) == {RACK}


def test_a_renamed_instrument_is_reported_before_anything_opens(tmp_path: Path, no_servers):
    project_dir = _iv_project(tmp_path)
    config_dir = tmp_path / "config"
    config = load_instruments(config_dir)
    config[RACK].children[MAINFRAME].children[SOURCE].attribute_name = "renamed_source"
    save_instruments_to_config(config, config_dir)

    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    with pytest.raises(ValueError, match="renamed or removed since the project was generated"):
        local_claims_for(project, project_dir, owner="iv")


def test_a_remote_override_claims_nothing_locally(tmp_path: Path):
    project_dir = _iv_project(tmp_path)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    assert local_claims_for(project, project_dir, owner="iv", remote="tcp://lab:12300") == []


# --------------------------- older and embedded projects ---------------------------


def test_a_project_with_its_own_instrument_copy_is_resolved_as_before(tmp_path: Path, no_servers):
    project_dir = _iv_project(tmp_path, style="pedagogical_embedded")
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    assert not uses_workspace_tree(project)

    assert resource_source_for(project, project_dir) is project.resources
    claims = local_claims_for(project, project_dir, owner="iv")
    assert set(claims[0].instruments) == set(project.resources.instruments)
