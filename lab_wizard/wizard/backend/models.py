from dataclasses import dataclass, field
from pydantic import BaseModel
from pathlib import Path
from typing import Any, Literal


ResourceKind = Literal["instrument", "saver", "plotter"]


class MeasurementInfo(BaseModel):
    """Information about available measurements"""

    name: str
    description: str
    measurement_dir: Path


class Env(BaseModel):

    base_dir: Path = Path(__file__).parent.parent.parent / "lib"
    instruments_dir: Path = base_dir / "instruments"
    measurements_dir: Path = base_dir / "measurements"
    projects_dir: Path = base_dir / "projects"



class MatchingReq(BaseModel):
    """A concrete instrument class that matches a required base type.

    Returned by discovery to populate UI choices.
    """

    module: str
    class_name: str
    qualname: str
    file_path: Path
    friendly_name: str


class ConfiguredResource(BaseModel):
    """A configured saver or plotter instance from the global registry."""

    type: str
    key: str
    fields: dict[str, Any]


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
    """In-memory requirement, populated by extraction and matching.

    For instrument resources, ``base_type`` is the Python class object and
    ``matching_instruments`` is filled by class-hierarchy discovery.
    For saver/plotter resources, ``matching_resources`` is filled from the
    configured registry.
    """

    variable_name: str
    base_type: Any
    resource_kind: ResourceKind = "instrument"
    is_list: bool = False
    matching_instruments: list[MatchingReq] = field(default_factory=list)
    matching_resources: list[ConfiguredResource] = field(default_factory=list)


class OutputReq(BaseModel):
    """JSON-serializable requirement returned to the frontend."""

    variable_name: str
    base_type: str
    resource_kind: ResourceKind = "instrument"
    is_list: bool = False
    matching_instruments: list[MatchingReq] = []
    matching_resources: list[ConfiguredResource] = []
    matching_remote: list[RemoteMatch] = []
