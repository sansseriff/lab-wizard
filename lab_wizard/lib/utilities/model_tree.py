from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Tuple, Optional
import yaml

from pydantic import BaseModel, Field, SerializeAsAny, model_validator
from ruamel.yaml import YAML as RuamelYAML

from lab_wizard.lib.utilities.params_discovery import (
    load_params_class,
    load_saver_params_class,
    load_plotter_params_class,
)


class Device(BaseModel):
    type: Literal["device"] = "device"
    name: str = Field(description="Name of the device")
    model: str = Field(description="Wafer")
    description: str = Field(description="Description of the device")


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


def _parse_flat_resource(data: Any, kind: Literal["saver", "plotter"]) -> Any:
    """Parse one entry of a flat resource dict (saver or plotter)."""
    if not isinstance(data, dict) or "type" not in data:
        return data
    type_str = data["type"]
    if kind == "saver":
        params_cls = load_saver_params_class(type_str)
    else:
        params_cls = load_plotter_params_class(type_str)
    return params_cls(**data)


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
    if isinstance(channels, list):
        for idx, ch_params in enumerate(channels):
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
    exp: "Exp",
    channel_index: Optional[int],
) -> Any:
    """Construct the full instrument chain for the given path and return the target."""
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
    """Project-level experiment configuration loaded from a project YAML.

    The ``savers`` and ``plotters`` dicts are name -> Params and are populated
    by auto-discovered Params classes; users can configure multiple of each.
    """

    exp: SerializeAsAny[BaseModel]
    device: Device | None = None
    savers: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)
    plotters: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)
    instruments: dict[str, SerializeAsAny[BaseModel]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _parse_dynamic_resources(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Parse instruments / savers / plotters using dynamic discovery before validation."""
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

        for kind, key_name in (("saver", "savers"), ("plotter", "plotters")):
            if key_name in out and isinstance(out[key_name], dict):
                parsed = {}
                for key, entry in out[key_name].items():
                    parsed[key] = _parse_flat_resource(entry, kind)  # type: ignore[arg-type]
                out[key_name] = parsed

        return out

    def from_attribute(self, attribute_name: str) -> Any:
        """Construct the instrument (or channel) that has the given attribute_name."""
        result = _find_attribute_path(self.instruments, attribute_name)
        if result is None:
            raise ValueError(
                f"No instrument with attribute_name={attribute_name!r} found in exp"
            )
        path, channel_index = result
        return _construct_from_path(path, self, channel_index)


def _rewrite_exp_yaml(yaml_path: Path | str, exp: Exp) -> None:
    """Rewrite the project YAML with corrected hash keys (in-place)."""
    y = RuamelYAML(typ="rt")
    y.default_flow_style = False

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = y.load(f)

    from lab_wizard.lib.utilities.config_io import (
        to_commented_yaml_value, model_to_commented_map,
    )

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

    After parsing, instrument hash keys are validated against the params they
    represent. If any key is stale (e.g. the user edited a ``slot`` or ``port``
    field), the key is recomputed and the YAML is rewritten in-place.
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
            pass

    return exp
