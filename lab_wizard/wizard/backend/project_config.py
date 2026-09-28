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
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError
from ruamel.yaml import YAML

from lab_wizard.lib.custom_measurements import load_custom_measurement
from lab_wizard.lib.procedures.storage import load_procedure
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
            return load_custom_measurement(project_dir / f"{name}.py").params_model
        return load_procedure(config_dir, name).params_model()
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
        "setup_file": setup[0].name if setup else None,
        "yaml": text,
        "run": project.run.model_dump(mode="json"),
        "params": project.measurement.params,
        "outputs": project.outputs.model_dump(mode="json"),
        # For the page to render and check the params form; None if unknown.
        "params_schema": model.model_json_schema() if model is not None else None,
    }


def save_project(projects_dir: Path, config_dir: Path, name: str, body: dict[str, Any]) -> dict[str, Any]:
    """Write a project's settings, from the whole YAML or from its sections.

    ``{"yaml": text}`` replaces the file as written. ``{"run", "params",
    "outputs"}`` (any of them) replace those sections and keep everything else
    in the file — its resources, and comments outside the sections changed.
    Nothing is written unless the result checks.
    """
    project_dir = project_dir_for(projects_dir, name)
    path = _yaml_path(project_dir)
    if "yaml" in body:
        text = str(body["yaml"])
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as e:
            raise ProjectConfigError([{"path": [], "message": f"not valid YAML: {e}"}]) from e
        _check(data, project_dir, config_dir)
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
        _check(yaml.safe_load(text), project_dir, config_dir)
    path.write_text(text, encoding="utf-8")
    return read_project(projects_dir, config_dir, name)
