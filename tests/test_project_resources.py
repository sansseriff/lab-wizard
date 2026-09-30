"""How a generated project finds its instruments when it runs.

Since instrument params left the project YAML (procedure plan 5.4), a project
names each instrument and resolves it against its workspace's config. (An
embedded-style project builds every instrument in its own setup file and uses
none of this.)
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from lab_wizard.lib.client import local_claims
from lab_wizard.lib.client.composite_resources import CompositeResources
from lab_wizard.lib.client.project_resources import (
    WorkspaceNotFound,
    local_claims_for,
    resource_source_for,
)
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.utilities.config_io import load_instruments, save_instruments_to_config
from lab_wizard.lib.utilities.model_tree import ResourceConfig, load_project_config
from lab_wizard.wizard.backend.procedure_generation import generate_procedure_project
from lab_wizard.wizard.backend.project_generation import GenerateProjectRequest


@pytest.fixture
def no_servers(monkeypatch):
    monkeypatch.setattr(local_claims, "preflight_local_project", lambda _instruments: None)


def _workspace(root: Path, rig) -> Path:
    config_dir = root / "config"
    rig.write(config_dir, ("source", "meter", "counter"))
    return config_dir


def _iv_project(root: Path, rig, style: str = "production") -> Path:
    config_dir = _workspace(root, rig)
    out = generate_procedure_project(
        config_dir=config_dir,
        projects_dir=root / "projects",
        req=GenerateProjectRequest(
            measurement_name="iv_curve",
            kind="procedure",
            generation_style=style,
            selected_resources=[rig.select("voltage_source", "source"), rig.select("voltage_sense", "meter")],
        ),
    )
    return Path(out["project_dir"])


def _names(config_dir: Path, rig) -> tuple[str, str]:
    config = load_instruments(config_dir)
    mainframe = config[rig.gpib].children[rig.mainframe]
    return mainframe.children[rig.source].attribute_name, mainframe.children[rig.meter].channels[0].attribute_name


# --------------------------- resolution ---------------------------


def test_a_project_resolves_its_instruments_from_the_workspace(tmp_path: Path, rig):
    project_dir = _iv_project(tmp_path, rig)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")

    source = resource_source_for(project, project_dir)
    assert isinstance(source, CompositeResources)
    source_name, _meter = _names(tmp_path / "config", rig)
    assert source.from_attribute(source_name) is not None


def test_two_instruments_in_one_rack_share_the_rack(tmp_path: Path, monkeypatch, rig):
    """Built twice, a rack would open its serial port twice, and the second
    open would be refused."""
    config_dir = _workspace(tmp_path, rig)
    built: list[str] = []
    original = PrologixGPIBParams.create_inst
    monkeypatch.setattr(PrologixGPIBParams, "create_inst", lambda self: built.append(self.port) or original(self))

    resources = ResourceConfig(instruments=load_instruments(config_dir))
    source_name, meter_name = _names(config_dir, rig)
    resources.from_attribute(source_name)
    resources.from_attribute(meter_name)

    assert built == [rig.port]


def test_a_bench_change_in_the_workspace_reaches_the_project_without_regenerating(tmp_path: Path, rig):
    """The point of not copying params: edit the workspace once, not every project."""
    project_dir = _iv_project(tmp_path, rig)
    config_dir = tmp_path / "config"
    config = load_instruments(config_dir)
    config[rig.gpib].children[rig.mainframe].children[rig.meter].channels[0].settling_time = 0.25
    save_instruments_to_config(config, config_dir)

    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    _source, meter_name = _names(config_dir, rig)
    meter = resource_source_for(project, project_dir).from_attribute(meter_name)
    assert meter.settling_time == 0.25


def test_outside_a_workspace_the_error_says_what_to_do(tmp_path: Path, rig):
    project_dir = _iv_project(tmp_path / "lab", rig)
    moved = tmp_path / "elsewhere" / project_dir.name
    shutil.copytree(project_dir, moved)
    project = load_project_config(moved / f"{moved.name}.yaml")

    with pytest.raises(WorkspaceNotFound, match="embedded style to make it self-contained"):
        resource_source_for(project, moved)


# --------------------------- claims ---------------------------


def test_only_the_racks_a_project_uses_are_claimed(tmp_path: Path, no_servers, rig):
    project_dir = _iv_project(tmp_path, rig)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")

    claims = local_claims_for(project, project_dir, owner="iv")
    assert len(claims) == 1
    # The rack both instruments live in — not the counter, which is also in
    # the workspace but not in this project.
    assert set(claims[0].instruments) == {rig.gpib}


def test_a_renamed_instrument_is_reported_before_anything_opens(tmp_path: Path, no_servers, rig):
    project_dir = _iv_project(tmp_path, rig)
    config_dir = tmp_path / "config"
    config = load_instruments(config_dir)
    config[rig.gpib].children[rig.mainframe].children[rig.source].attribute_name = "renamed_source"
    save_instruments_to_config(config, config_dir)

    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    with pytest.raises(ValueError, match="renamed or removed since the project was generated"):
        local_claims_for(project, project_dir, owner="iv")


def test_a_remote_override_claims_nothing_locally(tmp_path: Path, rig):
    project_dir = _iv_project(tmp_path, rig)
    project = load_project_config(project_dir / f"{project_dir.name}.yaml")
    assert local_claims_for(project, project_dir, owner="iv", remote="tcp://lab:12300") == []
