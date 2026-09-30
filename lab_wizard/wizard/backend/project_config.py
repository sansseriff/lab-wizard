"""A project's editable settings, for the Run page: read, check, save.

What a person changes between runs lives in the project YAML and is read
fresh at the start of every run, so editing it never means regenerating:

``run:``                 the device under test, operator, notes, and metadata —
                         free-form, nested groups allowed. Every scalar in it
                         becomes a Data page filter (``run.cryostat``,
                         ``run.optics.fiber``).
``measurement.params``   the measurement's settings, checked against its
                         params model (``param.bias.settle_s`` on the Data page).
``outputs:``             files and the live plot.

The page edits these as fields or as the whole YAML; either way the result is
checked as a whole before it is written, and every problem comes back with
the path of the field it is about.
"""

from __future__ import annotations

import io
from collections.abc import Collection
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from lab_wizard.lib.custom_measurements import load_custom_measurement
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.procedures.spec import ProcedureError
from lab_wizard.lib.project import build_measurement_module
from lab_wizard.lib.project_module import module_path
from lab_wizard.lib.utilities.model_tree import ProjectConfig
from lab_wizard.wizard.backend.project_generation import commented_params

__all__ = ["ProjectConfigError", "project_dir_for", "read_project", "save_project"]


class ProjectConfigError(ValueError):
    """What is wrong with a project's settings: ``[{"path": [...], "message"}]``."""

    def __init__(self, problems: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, p['path'])) or 'the file'}: {p['message']}" for p in problems))
        self.problems = problems


def project_dir_for(projects_dir: Path, name: str) -> Path:
    """The folder of the project called ``name``; ``KeyError`` if there is none."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise KeyError(f"No project named {name!r}")
    folder = projects_dir / name
    if not (folder / f"{name}.yaml").is_file():
        raise KeyError(f"No project named {name!r}")
    return folder


def _yaml_path(project_dir: Path) -> Path:
    return project_dir / f"{project_dir.name}.yaml"


def _params_model(project_dir: Path, project: ProjectConfig, config_dir: Path) -> type[BaseModel] | None:
    """The model ``measurement.params`` must fit, or ``None`` if it cannot be found."""
    name = project.project.measurement_type
    try:
        if project.project.kind == "custom":
            # The project's own copy: it is what runs, and it may have been edited.
            return load_custom_measurement(module_path(project_dir, name)).params_model
        if project.procedure is not None:
            # The project's own procedure, not the workspace's: it is what runs.
            return ProcedureDefinition.model_validate(project.procedure).params_model()
        return None
    except Exception:  # noqa: BLE001 - params then go unchecked here; the run still checks them
        return None


def _problems(error: ValidationError, prefix: list[Any]) -> list[dict[str, Any]]:
    return [{"path": [*prefix, *e["loc"]], "message": e["msg"]} for e in error.errors()]


def _check(data: Any, project_dir: Path, config_dir: Path) -> ProjectConfig:
    """``data`` as a project, or ``ProjectConfigError`` with every problem found."""
    if not isinstance(data, dict):
        raise ProjectConfigError([{"path": [], "message": "the file must be a mapping of sections"}])
    try:
        project = ProjectConfig.model_validate(data)
    except ValidationError as e:
        raise ProjectConfigError(_problems(e, [])) from e
    if project.procedure is not None:
        try:
            ProcedureDefinition.model_validate(project.procedure).check()
        except ValidationError as e:
            raise ProjectConfigError(_problems(e, ["procedure"])) from e
        except ProcedureError as e:
            raise ProjectConfigError([{"path": ["procedure"], "message": problem} for problem in e.problems]) from e
    model = _params_model(project_dir, project, config_dir)
    if model is not None:
        try:
            model.model_validate(project.measurement.params)
        except ValidationError as e:
            raise ProjectConfigError(_problems(e, ["measurement", "params"])) from e
    return project


def read_project(projects_dir: Path, config_dir: Path, name: str) -> dict[str, Any]:
    """Everything the Run page shows about a project's settings."""
    project_dir = project_dir_for(projects_dir, name)
    text = _yaml_path(project_dir).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    project = ProjectConfig.model_validate(data)
    model = _params_model(project_dir, project, config_dir)
    setup = sorted(project_dir.glob("*_setup.py"))
    return {
        "name": name,
        "path": str(project_dir),
        "measurement": project.project.measurement_type,
        "kind": project.project.kind,
        "style": project.project.style,
        "setup_file": setup[0].name if setup else None,
        "yaml": text,
        "run": project.run.model_dump(mode="json"),
        "params": project.measurement.params,
        "outputs": project.outputs.model_dump(mode="json"),
        # For the page to render and check the params form; None if unknown.
        "params_schema": model.model_json_schema() if model is not None else None,
    }


def _check_device(data: Any, known_devices: Collection[str] | None) -> None:
    """A run's device must be one the lab has registered, so a typo is not a new device."""
    if known_devices is None or not isinstance(data, dict):
        return
    device = (data.get("run") or {}).get("device")
    if device and device not in known_devices:
        raise ProjectConfigError([{
            "path": ["run", "device"],
            "message": f"no device named {device!r}; register it under Data → Devices first",
        }])


def save_project(
    projects_dir: Path,
    config_dir: Path,
    name: str,
    body: dict[str, Any],
    *,
    known_devices: Collection[str] | None = None,
) -> dict[str, Any]:
    """Write a project's settings, from the whole YAML or from its sections.

    ``{"yaml": text}`` replaces the file as written. ``{"run", "params",
    "outputs"}`` (any of them) replace those sections and keep everything else
    in the file — its resources, and comments outside the sections changed.
    Nothing is written unless the result checks. With ``known_devices``, the
    run's device must be one of them.
    """
    project_dir = project_dir_for(projects_dir, name)
    path = _yaml_path(project_dir)
    current = ProjectConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    if current.project.style == "embedded":
        setup = next(iter(sorted(project_dir.glob("*_setup.py"))), None)
        raise ProjectConfigError([{
            "path": [],
            "message": f"an embedded project's settings are written in {setup.name if setup else 'its setup file'}; edit them there",
        }])
    if "yaml" in body:
        text = str(body["yaml"])
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ProjectConfigError([{"path": [], "message": f"not valid YAML: {e}"}]) from e
        _check(data, project_dir, config_dir)
        _check_device(data, known_devices)
    else:
        rt = YAML(typ="rt")
        rt.default_flow_style = False
        document = rt.load(path.read_text(encoding="utf-8"))
        if "run" in body:
            document["run"] = body["run"]
        if "params" in body:
            model = _params_model(project_dir, ProjectConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))), config_dir)
            document.setdefault("measurement", {})["params"] = commented_params(body["params"], model)
        if "outputs" in body:
            document["outputs"] = body["outputs"]
        buffer = io.StringIO()
        rt.dump(document, buffer)
        text = buffer.getvalue()
        data = yaml.safe_load(text)
        _check(data, project_dir, config_dir)
        _check_device(data, known_devices)
    path.write_text(text, encoding="utf-8")
    # An edited procedure: block is built into <name>_measurement.py now, as a run would.
    build_measurement_module(project_dir)
    return read_project(projects_dir, config_dir, name)
