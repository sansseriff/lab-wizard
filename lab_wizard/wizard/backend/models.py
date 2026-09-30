from dataclasses import dataclass, field
from pydantic import BaseModel, ConfigDict
from pathlib import Path
from typing import Any


class Env(BaseModel):

    base_dir: Path = Path(__file__).parent.parent.parent / "lib"
    instruments_dir: Path = base_dir / "instruments"
    workspace_dir: Path | None = None
    config_dir: Path | None = None
    projects_dir: Path | None = None
    logs_dir: Path | None = None
    data_dir: Path | None = None
    # The workspace's custom measurements (lib/custom_measurements.py).
    measurements_dir: Path | None = None

    @classmethod
    def from_current_workspace(cls) -> "Env":
        from lab_wizard.lib.workspace import require_workspace

        workspace = require_workspace()
        return cls(
            workspace_dir=workspace.root,
            config_dir=workspace.config_dir,
            projects_dir=workspace.projects_dir,
            logs_dir=workspace.logs_dir,
            data_dir=workspace.data_dir,
            measurements_dir=workspace.measurements_dir,
        )


class MatchingReq(BaseModel):
    """A concrete instrument class that matches a required base type.

    Returned by discovery to populate UI choices.
    """

    module: str
    class_name: str
    qualname: str
    file_path: Path
    friendly_name: str


class RemoteMatch(BaseModel):
    """A named attribute on a registered remote server matching a requirement.

    Matched to a measurement's required resource type by ``behavior_abc`` — the
    same contract local discovery uses. Selecting one drives ``from_attribute``
    generation against ``url`` (see the ``--remote`` flow in setup templates).
    """

    server_name: str
    url: str
    attribute: str
    behavior_abc: str | None = None
    type_hint: str | None = None


@dataclass
class FilledReq:
    """In-memory requirement: one instrument role, populated by extraction and matching.

    ``base_type`` is the behavior class the role needs, and
    ``matching_instruments`` is filled by class-hierarchy discovery.
    """

    variable_name: str
    base_type: Any
    is_list: bool = False
    matching_instruments: list[MatchingReq] = field(default_factory=list)


class OutputReq(BaseModel):
    """JSON-serializable requirement returned to the frontend."""

    variable_name: str
    base_type: str
    is_list: bool = False
    matching_instruments: list[MatchingReq] = []
    matching_remote: list[RemoteMatch] = []


class ResponseModel(BaseModel):
    """Base for what a route returns: every field is present in its OpenAPI schema.

    Pydantic otherwise marks a field with a default as optional, so the
    frontend's generated type would say ``field?:`` for something the backend
    always sends.
    """

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
