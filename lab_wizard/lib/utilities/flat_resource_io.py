from __future__ import annotations

"""
Config I/O for flat resource registries (savers, plotters).

Mirrors the public surface of :mod:`config_io` (``load_*``, ``save_*_to_config``,
``add_*``, ``reset_*``, ``remove_*``, ``get_configured_*_tree``) but for
non-hierarchical resources where each entry is a single Params instance
identified by a user-given name. No parent/child, no hashing.

On-disk layout::

    config/
      savers/
        database_saver_key_main_db.yml
        file_saver_key_csv_backup.yml
      plotters/
        mpl_plotter_key_iv_window.yml
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Literal

from lab_wizard.lib.utilities.config_io import (
    _read_yaml, _write_yaml, model_to_commented_map,
)
from lab_wizard.lib.utilities.params_discovery import (
    Kind, load_params_class,
)


logger = logging.getLogger("lab_wizard.lib.utilities.flat_resource_io")


# Map kind to its on-disk subdirectory name.
ResourceKind = Literal["saver", "plotter"]
_KIND_DIR = {
    "saver": "savers",
    "plotter": "plotters",
}


def _resource_dir(config_dir: Path | str, kind: ResourceKind) -> Path:
    return (Path(config_dir) / _KIND_DIR[kind]).resolve()


def _node_filename(type_str: str, key: str) -> str:
    return f"{type_str}_key_{key}.yml"


def _params_class(kind: ResourceKind, type_str: str) -> type:
    """Load a Params class by type, dispatching by kind."""
    return load_params_class(type_str, kind=kind)


# ---------------------------- Loading ----------------------------


def load_resources(config_dir: Path | str, kind: ResourceKind) -> Dict[str, Any]:
    """Load all configured resources of the given kind into ``{key: params}``.

    Filenames look like ``<type>_key_<key>.yml``; the key is parsed back from the
    filename so it survives renames of the type field.  Resources with
    ``enabled: false`` are skipped.
    """
    inst_dir = _resource_dir(config_dir, kind)
    resources: Dict[str, Any] = {}

    if not inst_dir.exists():
        return resources

    for p in sorted(inst_dir.glob("*.yml")):
        data = _read_yaml(p)
        type_str = data.get("type")
        if not isinstance(type_str, str):
            logger.warning("Skipping %s: missing 'type' field", p)
            continue
        if data.get("enabled", True) is False:
            continue
        try:
            params_cls = _params_class(kind, type_str)
        except ValueError as e:
            logger.warning("Skipping %s: %s", p, e)
            continue
        params = params_cls(**data)
        key = _key_from_filename(p.stem, type_str)
        resources[key] = params

    logger.debug("Loaded %d %ss from %s", len(resources), kind, inst_dir)
    return resources


def _key_from_filename(stem: str, type_str: str) -> str:
    """Reverse ``<type>_key_<key>`` filename pattern to recover the key."""
    marker = f"{type_str}_key_"
    if stem.startswith(marker):
        return stem[len(marker):]
    return stem


# ---------------------------- Saving ----------------------------


def save_resource(config_dir: Path | str, kind: ResourceKind, key: str, params: Any) -> Path:
    """Write a single resource's YAML to the right path."""
    type_str = getattr(params, "type")
    inst_dir = _resource_dir(config_dir, kind)
    inst_dir.mkdir(parents=True, exist_ok=True)
    target = inst_dir / _node_filename(type_str, key)
    cm = model_to_commented_map(params, exclude_none=False, drop_enabled_true=True)
    _write_yaml(target, cm)
    return target


def save_resources_to_config(
    resources: Dict[str, Any], config_dir: Path | str, kind: ResourceKind
) -> None:
    """Write every entry in ``resources`` and remove orphaned files."""
    inst_dir = _resource_dir(config_dir, kind)

    files_to_keep: set[Path] = set()
    for key, params in (resources or {}).items():
        target = save_resource(config_dir, kind, key, params)
        files_to_keep.add(target)

    if inst_dir.exists():
        for f in sorted(inst_dir.glob("*.yml")):
            if f not in files_to_keep:
                try:
                    f.unlink()
                except OSError:
                    pass

    logger.info("Saved %d %ss to %s", len(resources), kind, inst_dir)


# ---------------------------- Tree (for frontend) ----------------------------


def _resource_to_tree_dict(key: str, params: Any) -> Dict[str, Any]:
    type_str = str(getattr(params, "type", ""))
    fields = params.model_dump()
    return {
        "type": type_str,
        "key": key,
        "fields": fields,
        "children": {},
    }


def get_configured_resources_tree(
    config_dir: Path | str, kind: ResourceKind
) -> List[Dict[str, Any]]:
    """Return a JSON-serializable list of resources for the frontend."""
    resources = load_resources(config_dir, kind)
    return [_resource_to_tree_dict(k, v) for k, v in resources.items()]


# ---------------------------- CRUD ----------------------------


def add_resource(
    config_dir: Path | str,
    kind: ResourceKind,
    type_str: str,
    key: str,
    overrides: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Create a new resource entry. ``overrides`` patches the default params."""
    params_cls = _params_class(kind, type_str)
    overrides = overrides or {}
    overrides.setdefault("type", type_str)
    params = params_cls(**overrides)
    save_resource(config_dir, kind, key, params)
    logger.info("Added %s type=%s key=%s", kind, type_str, key)
    return {
        "status": "ok",
        "tree": get_configured_resources_tree(config_dir, kind),
        "saved": {"type": type_str, "key": key},
    }


def reset_resource(
    config_dir: Path | str,
    kind: ResourceKind,
    type_str: str,
    key: str,
) -> Dict[str, Any]:
    """Reset a resource's fields to type defaults, keeping its key/filename."""
    params_cls = _params_class(kind, type_str)
    params = params_cls()
    save_resource(config_dir, kind, key, params)
    logger.info("Reset %s type=%s key=%s", kind, type_str, key)
    return {"status": "ok", "type": type_str, "key": key}


def remove_resource(
    config_dir: Path | str,
    kind: ResourceKind,
    type_str: str,
    key: str,
) -> Dict[str, Any]:
    """Delete a resource's YAML file."""
    inst_dir = _resource_dir(config_dir, kind)
    target = inst_dir / _node_filename(type_str, key)
    if not target.exists():
        raise ValueError(f"{kind} type={type_str} key={key} not found")
    try:
        target.unlink()
    except OSError as e:
        raise ValueError(f"Failed to remove {target}: {e}") from e
    logger.info("Removed %s type=%s key=%s", kind, type_str, key)
    return {
        "status": "ok",
        "type": type_str,
        "key": key,
        "tree": get_configured_resources_tree(config_dir, kind),
    }


def update_resource_fields(
    config_dir: Path | str,
    kind: ResourceKind,
    type_str: str,
    key: str,
    fields: Dict[str, Any],
) -> Dict[str, Any]:
    """Patch a single resource's fields and re-save."""
    params_cls = _params_class(kind, type_str)
    inst_dir = _resource_dir(config_dir, kind)
    target = inst_dir / _node_filename(type_str, key)
    if not target.exists():
        raise ValueError(f"{kind} type={type_str} key={key} not found")
    data = _read_yaml(target)
    data.update(fields)
    data["type"] = type_str  # keep type stable even if caller passed it
    params = params_cls(**data)
    save_resource(config_dir, kind, key, params)
    logger.info("Updated %s type=%s key=%s fields=%s", kind, type_str, key, sorted(fields))
    return {
        "status": "ok",
        "tree": get_configured_resources_tree(config_dir, kind),
        "saved": {"type": type_str, "key": key},
    }
