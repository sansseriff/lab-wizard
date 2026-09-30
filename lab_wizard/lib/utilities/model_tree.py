from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Tuple, Optional
import yaml

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, SerializeAsAny, model_validator

from lab_wizard.lib.utilities.resource_catalog import load_params_class


class ProjectInfo(BaseModel):
    schema_version: int = 1
    measurement_type: str
    # What measurement_type names: a procedure, or a custom measurement file.
    kind: Literal["procedure", "custom"] = "procedure"
    # ``embedded``: the setup file holds every setting and reads nothing from
    # this YAML or the workspace (wizard/backend/embedded_generation.py).
    style: Literal["production", "embedded"] = "production"
    created_by: str = "lab_wizard"


class RunConfig(BaseModel):
    """Who and what a run is about. Read at the start of every run and recorded with it."""

    # Every field is present in what the wizard's API returns (its OpenAPI schema).
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    device: str | None = Field(default=None, description="The device under test, by its name in the lab database")
    operator: str | None = None
    notes: str | None = None
    # Anything else worth filtering runs by later: {"cryostat": "BlueFors1"}.
    metadata: dict[str, Any] = Field(default_factory=dict)


class MeasurementConfig(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class OutputsConfig(BaseModel):
    """What a run produces besides its record in the lab database, which is always written."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    # Also save each run as a folder of files, laid out by the workspace's
    # data settings (lab_wizard.lib.data.settings).
    files: bool = True
    # How a run started from a terminal is drawn while it goes. A run the
    # wizard launches is drawn on its Run page whatever this says.
    live_plot: Literal["none", "window", "web"] = "none"
    # Which of the procedure's plots: to draw live; empty for the first.
    plot: str = ""


class ResourceConfig(BaseModel):
    instruments: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)

    # Where each named instrument comes from: "local", or the name of a server
    # in config/remote/servers.yaml. Recorded here rather than passed as a
    # command-line flag so a project runs identically for everyone and stays
    # reproducible. Attributes absent from this mapping are local.
    instrument_sources: dict[str, str] = Field(default_factory=dict)

    # Instruments already built, by hash-key path from the root. Two attributes
    # under one rack must share the rack: building it twice would open its
    # serial port twice, and the port admits one holder.
    _built: dict[tuple[str, ...], Any] = PrivateAttr(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _parse_dynamic_resources(cls, data: dict[str, Any]) -> dict[str, Any]:
        return _parse_resource_sections(data)

    def from_attribute(self, attribute_name: str) -> Any:
        result = _find_attribute_path(self.instruments, attribute_name)
        if result is None:
            raise ValueError(f"No instrument with attribute_name={attribute_name!r} found in resources.")
        path, channel_index = result
        return _construct_from_path(path, channel_index, self._built)


class RoleBinding(BaseModel):
    """The instrument that fills one role: its ``attribute_name``, and where it lives.

    ``server`` is ``None`` for an instrument in this workspace's
    config/instruments, or the name of a server in config/remote/servers.yaml.
    In the YAML a local binding is written as just the name.
    """

    instrument: str
    server: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_name(cls, data: Any) -> Any:
        return {"instrument": data} if isinstance(data, str) else data

    def as_yaml(self) -> Any:
        return self.instrument if self.server is None else {"instrument": self.instrument, "server": self.server}


class ProjectConfig(BaseModel):
    project: ProjectInfo
    run: RunConfig = Field(default_factory=RunConfig)
    measurement: MeasurementConfig = Field(default_factory=MeasurementConfig)
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)
    # Which instrument fills each of the measurement's roles; a list for a role
    # that takes several. The setup file says what class each one is.
    roles: dict[str, RoleBinding | list[RoleBinding]] = Field(default_factory=dict)
    # A composed procedure's definition, as a procedure file has it: its roles,
    # params (types, units, defaults), step tree, derived columns and plots.
    # The one place a project's procedure is edited; <name>_measurement.py is
    # built from it (lab_wizard.lib.project). None for a custom measurement.
    procedure: dict[str, Any] | None = None
    # A custom resource file names its instruments here instead of by role.
    resources: ResourceConfig = Field(default_factory=ResourceConfig)

    @property
    def measurement_type(self) -> str:
        return self.project.measurement_type

    def bindings(self) -> list[RoleBinding]:
        """Every instrument the roles are bound to, in role order."""
        out: list[RoleBinding] = []
        for binding in self.roles.values():
            out.extend(binding if isinstance(binding, list) else [binding])
        return out

    def sources(self) -> dict[str, str]:
        """``{attribute_name: "local" | server}`` for every instrument this project uses."""
        out = {b.instrument: b.server or "local" for b in self.bindings()}
        out.update(self.resources.instrument_sources)
        return out


def _parse_instrument_tree(data: dict[str, Any]) -> Any:
    """Recursively parse an instrument dict into the correct Params class.

    Uses dynamic discovery to find the right class based on the 'type' field.
    Handles nested 'children' dicts recursively.
    """
    if not isinstance(data, dict) or "type" not in data:
        return data

    type_str = data["type"]

    if "children" in data and isinstance(data["children"], dict):
        parsed_children = {}
        for key, child_data in data["children"].items():
            parsed_children[key] = _parse_instrument_tree(child_data)
        data = {**data, "children": parsed_children}

    params_cls = load_params_class(type_str)
    return params_cls(**data)


def _parse_resource_sections(data: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        return data

    out = dict(data)

    if "instruments" in out and isinstance(out["instruments"], dict):
        parsed = {}
        for key, inst_data in out["instruments"].items():
            if isinstance(inst_data, dict) and "type" in inst_data:
                parsed[key] = _parse_instrument_tree(inst_data)
            else:
                parsed[key] = inst_data
        out["instruments"] = parsed

    return out


# --------------- attribute_name search helpers ---------------


def _find_attribute_path(
    instruments: dict[str, Any],
    attribute_name: str,
) -> Optional[Tuple[list[Tuple[str, Any]], Optional[int]]]:
    """Depth-first search for a node or channel with the given attribute_name."""
    for root_key, root_params in instruments.items():
        result = _search_node(
            key=root_key,
            params=root_params,
            attribute_name=attribute_name,
            ancestors=[],
        )
        if result is not None:
            return result
    return None


def _search_node(
    key: str,
    params: Any,
    attribute_name: str,
    ancestors: list[Tuple[str, Any]],
) -> Optional[Tuple[list[Tuple[str, Any]], Optional[int]]]:
    path = ancestors + [(key, params)]

    if getattr(params, "attribute_name", None) == attribute_name:
        return path, None

    channels = getattr(params, "channels", None)
    if isinstance(channels, dict):
        for idx, ch_params in channels.items():
            if getattr(ch_params, "attribute_name", None) == attribute_name:
                return path, idx

    children = getattr(params, "children", None)
    if isinstance(children, dict):
        for child_key, child_params in children.items():
            result = _search_node(child_key, child_params, attribute_name, path)
            if result is not None:
                return result

    return None


def _construct_from_path(
    path: list[Tuple[str, Any]],
    channel_index: Optional[int],
    built: Optional[dict[tuple[str, ...], Any]] = None,
) -> Any:
    """Construct the instrument chain for ``path`` and return the target.

    ``built`` carries nodes constructed by earlier calls, keyed by their path of
    hash keys, so a root or mainframe shared by several attributes is built once.
    """
    if not path:
        raise ValueError("Empty path — cannot construct instrument")
    cache = built if built is not None else {}

    root_key, root_params = path[0]
    if not hasattr(root_params, "create_inst"):
        raise TypeError(
            f"Root params {type(root_params).__name__} does not support create_inst(); "
            "top-level instruments must inherit CanInstantiate."
        )
    keys: tuple[str, ...] = (root_key,)
    if keys not in cache:
        cache[keys] = root_params.create_inst()
    current_inst = cache[keys]

    for hash_key, _params in path[1:]:
        keys = (*keys, hash_key)
        if keys not in cache:
            cache[keys] = current_inst.make_child(hash_key)
        current_inst = cache[keys]

    if channel_index is not None:
        channels = getattr(current_inst, "channels", None)
        if channels is None or channel_index >= len(channels):
            raise IndexError(
                f"channel_index {channel_index} out of range for "
                f"{type(current_inst).__name__}"
            )
        return channels[channel_index]

    return current_inst


def load_project_config(yaml_path: str | Path) -> ProjectConfig:
    """Load the explicit project YAML shape used by generated projects."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ProjectConfig.model_validate(data)
