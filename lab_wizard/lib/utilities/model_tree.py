from __future__ import annotations

from pathlib import Path
from typing import Any, List, Annotated, Literal, Tuple, Optional
import yaml

from pydantic import BaseModel, Field, SerializeAsAny, model_validator
from ruamel.yaml import YAML as RuamelYAML

from lab_wizard.lib.utilities.params_discovery import load_params_class


class FileSaver(BaseModel):
    type: Literal["file_saver"] = "file_saver"
    file_path: str = Field(description="Path to save the file")
    include_timestamp: bool = Field(
        default=True, description="Include timestamp in the filename"
    )
    include_metadata: bool = Field(
        default=True, description="Include metadata in the saved file"
    )


class DatabaseSaver(BaseModel):
    type: Literal["database_saver"] = "database_saver"
    db_url: str = Field(description="Database connection URL")
    table_name: str = Field(description="Name of the table to save data")
    include_metadata: bool = Field(
        default=True, description="Include metadata in the saved data"
    )


class WebPlotter(BaseModel):
    type: Literal["web_plotter"] = "web_plotter"
    url: str = Field(description="URL of the web service to send plot data")


class MplPlotter(BaseModel):
    type: Literal["mpl_plotter"] = "mpl_plotter"
    figure_size: List[int] = Field(
        default=[10, 6], description="Size of the matplotlib figure in inches"
    )
    dpi: int = Field(default=100, description="DPI for the matplotlib figure")


class IVCurveParams(BaseModel):
    type: Literal["iv_curve"] = "iv_curve"
    start_voltage: float = Field(description="Start voltage for the IV curve")
    stop_voltage: float = Field(description="Stop voltage for the IV curve")
    step_voltage: float = Field(description="Voltage step size for the IV curve")
    num_points: int = Field(default=100, description="Number of points in the IV curve")


class PCRCurveParams(BaseModel):
    type: Literal["pcr_curve"] = "pcr_curve"
    start_voltage: float = Field(description="Start voltage for the PCR curve")
    stop_voltage: float = Field(description="Stop voltage for the PCR curve")
    step_voltage: float = Field(description="Voltage step size for the PCR curve")
    photon_rate: float = Field(description="Number of photons per second")


class Device(BaseModel):
    type: Literal["device"] = "device"
    name: str = Field(description="Name of the device")
    model: str = Field(description="Wafer")
    description: str = Field(description="Description of the device")


SaverUnion = Annotated[FileSaver | DatabaseSaver, Field(discriminator="type")]
PlotterUnion = Annotated[WebPlotter | MplPlotter, Field(discriminator="type")]
ExpUnion = Annotated[IVCurveParams | PCRCurveParams, Field(discriminator="type")]


def _parse_instrument_tree(data: dict[str, Any]) -> Any:
    """
    Recursively parse an instrument dict into the correct Params class.

    Uses dynamic discovery to find the right class based on the 'type' field.
    Handles nested 'children' dicts recursively.
    """
    if not isinstance(data, dict) or "type" not in data:
        return data

    type_str = data["type"]

    # Parse children recursively first
    if "children" in data and isinstance(data["children"], dict):
        parsed_children = {}
        for key, child_data in data["children"].items():
            parsed_children[key] = _parse_instrument_tree(child_data)
        data = {**data, "children": parsed_children}

    # Load the Params class and instantiate
    params_cls = load_params_class(type_str)
    return params_cls(**data)


# --------------- attribute_name search helpers ---------------


def _find_attribute_path(
    instruments: dict[str, Any],
    attribute_name: str,
) -> Optional[Tuple[list[Tuple[str, Any]], Optional[int]]]:
    """Depth-first search for a node or channel with the given attribute_name.

    Returns ``(path, channel_index)`` where:
      - ``path`` is a list of ``(hash_key, params)`` tuples from root to leaf
      - ``channel_index`` is ``None`` unless the match is a channel list item

    Returns ``None`` if not found.
    """
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

    # Check this node's own attribute_name field
    if getattr(params, "attribute_name", None) == attribute_name:
        return path, None

    # Check channel list items (for instruments like Keysight53220A, Sim970)
    channels = getattr(params, "channels", None)
    if isinstance(channels, list):
        for idx, ch_params in enumerate(channels):
            if getattr(ch_params, "attribute_name", None) == attribute_name:
                return path, idx

    # Recurse into children
    children = getattr(params, "children", None)
    if isinstance(children, dict):
        for child_key, child_params in children.items():
            result = _search_node(child_key, child_params, attribute_name, path)
            if result is not None:
                return result

    return None


def _construct_from_path(
    path: list[Tuple[str, Any]],
    exp: "Exp",
    channel_index: Optional[int],
) -> Any:
    """Construct the full instrument chain for the given path and return the target.

    ``path`` is a list of ``(hash_key, params)`` from root → leaf.
    The root instrument is created via ``params.create_inst()`` (CanInstantiate).
    Each subsequent level is created via ``parent_inst.make_child(key)``.
    If ``channel_index`` is not None, returns ``leaf_inst.channels[channel_index]``.
    """
    if not path:
        raise ValueError("Empty path — cannot construct instrument")

    root_key, root_params = path[0]
    if not hasattr(root_params, "create_inst"):
        raise TypeError(
            f"Root params {type(root_params).__name__} does not support create_inst(); "
            "top-level instruments must inherit CanInstantiate."
        )
    current_inst = root_params.create_inst()

    for hash_key, _params in path[1:]:
        current_inst = current_inst.make_child(hash_key)

    if channel_index is not None:
        channels = getattr(current_inst, "channels", None)
        if channels is None or channel_index >= len(channels):
            raise IndexError(
                f"channel_index {channel_index} out of range for "
                f"{type(current_inst).__name__}"
            )
        return channels[channel_index]

    return current_inst


class Exp(BaseModel):
    exp: ExpUnion
    device: Device
    saver: dict[str, SaverUnion]
    plotter: dict[str, PlotterUnion]
    # Instruments are loaded dynamically via params_discovery - no static union needed
    instruments: dict[str, SerializeAsAny[BaseModel]]

    @model_validator(mode="before")
    @classmethod
    def _parse_instruments_dynamically(cls, data: dict[str, Any]) -> dict[str, Any]:
        """
        Parse instruments using dynamic discovery before Pydantic validation.

        This allows the YAML to contain any registered instrument type without
        needing a static union that imports all Params classes upfront.
        """
        if "instruments" not in data or not isinstance(data["instruments"], dict):
            return data

        parsed_instruments = {}
        for key, inst_data in data["instruments"].items():
            if isinstance(inst_data, dict) and "type" in inst_data:
                parsed_instruments[key] = _parse_instrument_tree(inst_data)
            else:
                # Already parsed or not a dict - pass through
                parsed_instruments[key] = inst_data

        return {**data, "instruments": parsed_instruments}

    def from_attribute(self, attribute_name: str) -> Any:
        """Construct the instrument (or channel) that has the given attribute_name.

        Walks the instruments tree depth-first, finds the node or channel
        whose ``attribute_name`` param matches, then builds the full parent
        chain top-down and returns the target instrument.

        Example::

            exp = load_exp_from_yaml("my_exp.yaml")
            sim928 = exp.from_attribute("my_favorit_sim928")
            counter_ch = exp.from_attribute("my_keysight53_channel")
        """
        result = _find_attribute_path(self.instruments, attribute_name)
        if result is None:
            raise ValueError(
                f"No instrument with attribute_name={attribute_name!r} found in exp"
            )
        path, channel_index = result
        return _construct_from_path(path, self, channel_index)

    def find_all_resources(self) -> dict[str, tuple[str, object]]:
        """Find all resources in the experiment tree.
        Returns dict mapping resource_id -> (access_path, object)."""
        resources = {}

        for inst_key, instrument in self.instruments.items():
            base_path = f"exp.instruments['{inst_key}']"

            # Check if the instrument itself has a resource
            if hasattr(instrument, "has_resource") and instrument.has_resource():
                resource_id = instrument.get_resource_id()
                resources[resource_id] = (base_path, instrument)

            # Check for nested resources (e.g., in mainframes)
            if hasattr(instrument, "find_resources"):
                nested_resources = instrument.find_resources(base_path)
                for resource_id, path, obj in nested_resources:
                    resources[resource_id] = (path, obj)

        return resources


def _rewrite_exp_yaml(yaml_path: Path | str, exp: Exp) -> None:
    """Rewrite the project YAML with corrected hash keys (in-place)."""
    import json

    y = RuamelYAML(typ="rt")
    y.default_flow_style = False

    # Load original for round-trip preservation of non-instruments keys
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = y.load(f)

    # Rebuild instruments section from repaired Exp
    from lab_wizard.lib.utilities.config_io import to_commented_yaml_value, model_to_commented_map

    repaired_instruments = {}
    for k, v in exp.instruments.items():
        if isinstance(v, BaseModel):
            repaired_instruments[k] = model_to_commented_map(v, exclude_none=True)
        else:
            repaired_instruments[k] = v

    data["instruments"] = to_commented_yaml_value(repaired_instruments)

    with open(yaml_path, "w", encoding="utf-8") as f:
        y.dump(data, f)


def load_exp_from_yaml(yaml_path: str | Path) -> Exp:
    """Load an Exp object from a project YAML file.

    After parsing, hash keys are validated against the params they represent.
    If any key is stale (e.g. the user edited a ``slot`` or ``port`` field),
    the key is recomputed and the YAML is rewritten in-place so future loads
    are clean.
    """
    from lab_wizard.lib.utilities.config_io import validate_and_repair_hashes

    yaml_path = Path(yaml_path)
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    exp = Exp.model_validate(data)

    repaired, changed = validate_and_repair_hashes(exp.instruments)
    if changed:
        exp = exp.model_copy(update={"instruments": repaired})
        try:
            _rewrite_exp_yaml(yaml_path, exp)
        except OSError:
            # Read-only filesystem or test environment — skip rewrite
            pass

    return exp
