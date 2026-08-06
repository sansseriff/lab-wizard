"""Compatibility facade for the automatic runtime resource catalog.

New code should import :mod:`resource_catalog`.  These names remain while the
server, wizard, and configuration loaders migrate without a wire/YAML break.
"""

from __future__ import annotations

from typing import Any

from lab_wizard.lib.utilities import resource_catalog as _catalog
from lab_wizard.lib.utilities.resource_catalog import (
    Kind,
    ResourceAuditError,
    clear_cache,
    get_metadata,
    get_parent_chain,
    list_available_types,
    load_params_class,
)

# Transitional aliases retained for tests and callers that inspect process
# state.  The generated on-disk format itself is intentionally new/versioned.
_loaded_params = _catalog._loaded_params
_type_to_module = _catalog._source_maps
_metadata_cache = _catalog._metadata


def get_type_to_module_map(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    return _catalog.get_source_map(kind)


def load_saver_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, kind="saver", verbose=verbose)


def load_plotter_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, kind="plotter", verbose=verbose)


def get_instrument_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("instrument")


def get_saver_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("saver")


def get_plotter_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("plotter")


def get_config_folder(params_cls: type) -> str | None:
    module = params_cls.__module__
    prefix = "lab_wizard.lib.instruments."
    if not module.startswith(prefix):
        return None
    suffix = module[len(prefix):]
    parts = suffix.split(".")
    if parts[0] == "general":
        return None
    folder_parts = parts[:-1]
    return "/".join(folder_parts) if folder_parts else None
