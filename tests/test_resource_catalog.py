from __future__ import annotations

from pathlib import Path

import pytest

from lab_wizard.lib.utilities import resource_catalog


def _reset_process_state() -> None:
    for kind in ("instrument", "step"):
        resource_catalog._source_maps[kind] = None
        resource_catalog._source_signatures[kind] = None
        resource_catalog._source_fingerprints[kind] = None
        resource_catalog._loaded_params[kind] = {}
        resource_catalog._stale_loaded[kind] = set()
        resource_catalog._metadata[kind] = None
        resource_catalog._metadata_signatures[kind] = None


def test_ast_index_finds_new_params_file_without_importing(tmp_path, monkeypatch):
    root = tmp_path / "instruments"
    root.mkdir()
    candidate = root / "new_driver.py"
    candidate.write_text(
        """
from typing import Literal

class NewDriverParams(
    SomeParamsBase,
):
    type: Literal[
        "new_driver"
    ] = "new_driver"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(resource_catalog, "_root_dir", lambda kind: root)
    _reset_process_state()

    index = resource_catalog.get_source_map("instrument")

    assert index["new_driver"] == {
        "module": "lab_wizard.lib.instruments.new_driver",
        "class_name": "NewDriverParams",
        "kind": "instrument",
        "source_file": "new_driver.py",
    }


def test_source_index_notices_a_file_added_after_first_lookup(tmp_path, monkeypatch):
    root = tmp_path / "steps"
    root.mkdir()
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(resource_catalog, "_root_dir", lambda kind: root)
    _reset_process_state()
    assert resource_catalog.get_source_map("step") == {}

    (root / "later.py").write_text(
        'from typing import Literal\nclass LaterParams:\n    type: Literal["later"] = "later"\n',
        encoding="utf-8",
    )

    assert "later" in resource_catalog.get_source_map("step")


def test_source_index_reparses_only_changed_files(tmp_path, monkeypatch):
    root = tmp_path / "steps"
    root.mkdir()
    for name in ("first", "second"):
        (root / f"{name}.py").write_text(
            f'from typing import Literal\nclass {name.title()}Params:\n'
            f'    type: Literal["{name}"] = "{name}"\n',
            encoding="utf-8",
        )
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(resource_catalog, "_root_dir", lambda kind: root)
    _reset_process_state()
    resource_catalog.get_source_map("step")

    original_scan = resource_catalog._scan_file
    scanned: list[str] = []

    def recording_scan(path: Path, kind):
        scanned.append(path.name)
        return original_scan(path, kind)

    monkeypatch.setattr(resource_catalog, "_scan_file", recording_scan)
    second = root / "second.py"
    second.write_text(second.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert set(resource_catalog.get_source_map("step")) == {"first", "second"}
    assert scanned == ["second.py"]


def test_changed_already_loaded_module_requires_process_restart(tmp_path, monkeypatch):
    root = tmp_path / "instruments"
    root.mkdir()
    driver = root / "driver.py"
    driver.write_text(
        'from typing import Literal\nclass DriverParams:\n'
        '    type: Literal["driver"] = "driver"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(resource_catalog, "_root_dir", lambda kind: root)
    _reset_process_state()
    resource_catalog.get_source_map("instrument")
    resource_catalog._loaded_params["instrument"]["driver"] = object

    driver.write_text(driver.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    resource_catalog.get_source_map("instrument")

    with pytest.raises(resource_catalog.ResourceAuditError, match="Restart the process"):
        resource_catalog.load_params_class("driver")


def test_warm_metadata_cache_does_not_import_resource_modules(tmp_path, monkeypatch):
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path))
    _reset_process_state()
    expected = resource_catalog.get_metadata("instrument")
    resource_catalog._metadata["instrument"] = None
    resource_catalog._metadata_signatures["instrument"] = None
    resource_catalog._source_maps["instrument"] = None
    resource_catalog._source_signatures["instrument"] = None
    resource_catalog._source_fingerprints["instrument"] = None
    resource_catalog._loaded_params["instrument"] = {}

    def unexpected_import(_name: str):
        raise AssertionError("warm metadata lookup imported a resource module")

    monkeypatch.setattr(resource_catalog.importlib, "import_module", unexpected_import)

    assert resource_catalog.get_metadata("instrument") == expected


def test_runtime_catalog_uses_python_hierarchy_and_inherited_channel_class(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("LAB_WIZARD_CACHE_DIR", str(tmp_path))
    _reset_process_state()

    metadata = resource_catalog.get_metadata("instrument")

    assert metadata["sim970"]["parent_chain"] == ["sim900", "prologix_gpib"]
    assert metadata["sim970"]["channel_behavior_abc"] == "VSense"


def test_parent_params_discriminate_raw_children_through_catalog():
    from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
    from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params

    rack = Sim900Params.model_validate({
        "type": "sim900",
        "children": {"meter": {"type": "sim970", "slot": 2}},
    })

    assert isinstance(rack.children["meter"], Sim970Params)


def test_parent_params_reject_registered_child_from_wrong_nominal_family():
    from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params

    with pytest.raises(ValueError):
        Sim900Params.model_validate({
            "type": "sim900",
            "children": {"attenuator": {"type": "yoko_attenuator", "slot": 2}},
        })
