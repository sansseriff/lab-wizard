"""
Auto-discovery of Params classes from lib/instruments/, lib/savers/, lib/plotters/.

Uses a JSON cache for fast lookups, rebuilds cache only when the relevant folder
changes.  Supports three "kinds" of resource: ``"instrument"``, ``"saver"``,
``"plotter"``.  Instruments use the parent/child hierarchy (CanInstantiate,
ChildParams).  Savers and plotters are flat — anything inheriting SaverParams /
PlotterParams with a ``type: Literal[...]`` field is registered.

Usage:
    from lab_wizard.lib.utilities.params_discovery import (
        load_params_class, load_saver_params_class, load_plotter_params_class,
        get_instrument_metadata, get_saver_metadata, get_plotter_metadata,
    )

    # Load a Params class by its type string (kind defaults to instrument)
    params_cls = load_params_class("dbay")
    saver_cls  = load_saver_params_class("database_saver")
"""
from __future__ import annotations

import importlib
import json
import re
import logging
from pathlib import Path
from typing import Any, Literal


Kind = Literal["instrument", "saver", "plotter"]

# Files/folders to skip during scanning (utilities, not resource definitions)
SKIP_NAMES = {
    "__init__.py",
    "comm.py",
    "deps.py",
    "state.py",
    "addons",
    "__pycache__",
}

# Cache locations — one per kind so they invalidate independently.
CACHE_DIR = Path.home() / ".cache" / "lab_wizard"

# (root_subpath_under_lib, kind_name, allowed_base_class_names)
# A class is registered if it inherits from at least one of the listed bases AND
# carries a ``type: Literal[...]`` field.  For instruments the base class also
# tells us whether it is a top-level (CanInstantiate) or child (ChildParams).
_KIND_SPECS: dict[str, dict[str, Any]] = {
    "instrument": {
        "subpath": "instruments",
        "bases": ("CanInstantiate", "ChildParams"),
        "has_hierarchy": True,
    },
    "saver": {
        "subpath": "savers",
        "bases": ("SaverParams",),
        "has_hierarchy": False,
    },
    "plotter": {
        "subpath": "plotters",
        "bases": ("PlotterParams",),
        "has_hierarchy": False,
    },
}

logger = logging.getLogger("lab_wizard.lib.utilities.params_discovery")

# In-memory caches keyed by kind.
_loaded_params: dict[str, dict[str, type]] = {k: {} for k in _KIND_SPECS}
_type_to_module: dict[str, dict[str, dict[str, Any]] | None] = {k: None for k in _KIND_SPECS}
_metadata_cache: dict[str, dict[str, dict[str, Any]] | None] = {k: None for k in _KIND_SPECS}


def _root_dir(kind: Kind) -> Path:
    """Get the source root directory for a given kind (e.g. .../lib/instruments)."""
    spec = _KIND_SPECS[kind]
    return (Path(__file__).parent.parent / spec["subpath"]).resolve()


def _cache_file(kind: Kind) -> Path:
    return CACHE_DIR / f"params_cache_{kind}.json"


def _module_prefix(kind: Kind) -> str:
    spec = _KIND_SPECS[kind]
    return f"lab_wizard.lib.{spec['subpath']}."


def _get_folder_fingerprint(root_dir: Path) -> tuple[float, int]:
    """Get (max_mtime, file_count) for cache invalidation."""
    if not root_dir.exists():
        return 0.0, 0
    max_mtime = root_dir.stat().st_mtime
    file_count = 0
    for py_file in root_dir.rglob("*.py"):
        file_count += 1
        mtime = py_file.stat().st_mtime
        if mtime > max_mtime:
            max_mtime = mtime
    return max_mtime, file_count


def _should_skip(path: Path) -> bool:
    """Check if file/folder should be skipped during scanning."""
    return path.name in SKIP_NAMES or any(skip in path.parts for skip in SKIP_NAMES)


_CLASS_WITH_BASES = re.compile(r'class\s+(\w+Params)\s*\(([^)]+)\)', re.MULTILINE)
_TYPE_LITERAL = re.compile(r'type:\s*Literal\[(["\'])([^"\']+)\1\]')
# Reads the return string of each Child's `parent_class` property from source.
# This is the canonical way parent-child relationships are discovered —
# see Child.parent_class docstring in parent_child.py.
_PARENT_CLASS_RETURN = re.compile(
    r'def\s+parent_class\s*\([^)]*\)[^:]*:.*?return\s+["\']([^"\']+)["\']',
    re.DOTALL,
)


def _scan_file_for_params(
    path: Path, root_dir: Path, kind: Kind
) -> list[dict[str, Any]]:
    """Scan a Python file for Params classes with type Literal fields.

    Returns list of dicts with keys:
        type_value, module, class_name, is_top_level, is_child, parent_module
    For non-instrument kinds, is_top_level/is_child/parent_module are filled
    with neutral defaults (True/False/None).
    """
    results: list[dict[str, Any]] = []

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return results

    if "Params" not in content:
        return results

    spec = _KIND_SPECS[kind]
    base_names: tuple[str, ...] = spec["bases"]

    class_iter = list(_CLASS_WITH_BASES.finditer(content))
    if not class_iter:
        return results

    try:
        rel_path = path.relative_to(root_dir)
        prefix = _module_prefix(kind)
        module_path = prefix + ".".join(rel_path.with_suffix("").parts)
    except ValueError:
        return results

    parent_module: str | None = None
    if spec["has_hierarchy"]:
        parent_matches = _PARENT_CLASS_RETURN.findall(content)
        if parent_matches:
            raw = parent_matches[0]
            if not raw.startswith("lab_wizard."):
                raw = "lab_wizard." + raw
            parent_module = raw.rsplit(".", 1)[0]

    for i, match in enumerate(class_iter):
        class_name, bases_str = match.group(1), match.group(2)
        # Match by base-class name. For instruments this distinguishes
        # CanInstantiate (top-level) from ChildParams (child); for savers/
        # plotters we only check that SaverParams/PlotterParams is present.
        if spec["has_hierarchy"]:
            is_top_level = bool(re.search(r'\bCanInstantiate\b', bases_str))
            is_child = bool(re.search(r'\bChildParams\b', bases_str))
            if not is_top_level and not is_child:
                continue
        else:
            matches_base = any(
                re.search(rf'\b{re.escape(b)}\b', bases_str) for b in base_names
            )
            if not matches_base:
                continue
            is_top_level = True
            is_child = False

        body_start = match.end()
        body_end = class_iter[i + 1].start() if i + 1 < len(class_iter) else len(content)
        class_body = content[body_start:body_end]
        type_match = _TYPE_LITERAL.search(class_body)
        if not type_match:
            continue
        results.append({
            "type_value": type_match.group(2),
            "module": module_path,
            "class_name": class_name,
            "is_top_level": is_top_level,
            "is_child": is_child,
            "parent_module": parent_module,
        })

    return results


def _scan_root(kind: Kind) -> dict[str, dict[str, Any]]:
    """Scan the source root for a given kind for Params classes.

    Returns mapping: type_string -> {module, class_name, is_top_level,
    is_child, parent_module, parent_type, kind}
    """
    root_dir = _root_dir(kind)
    type_to_module: dict[str, dict[str, Any]] = {}

    if not root_dir.exists():
        return type_to_module

    for py_file in root_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue

        found = _scan_file_for_params(py_file, root_dir, kind)

        for entry in found:
            tv = entry["type_value"]
            if tv in type_to_module:
                existing = type_to_module[tv]
                if existing["module"] != entry["module"] or existing["class_name"] != entry["class_name"]:
                    raise ValueError(
                        f"Duplicate type '{tv}' (kind={kind}) found in "
                        f"{existing['module']}.{existing['class_name']} and "
                        f"{entry['module']}.{entry['class_name']}"
                    )
            type_to_module[tv] = {
                "module": entry["module"],
                "class_name": entry["class_name"],
                "is_top_level": entry["is_top_level"],
                "is_child": entry["is_child"],
                "parent_module": entry["parent_module"],
                "kind": kind,
            }

    if _KIND_SPECS[kind]["has_hierarchy"]:
        module_to_type: dict[str, str] = {
            v["module"]: k for k, v in type_to_module.items()
        }
        for info in type_to_module.values():
            pm = info.get("parent_module")
            if pm and pm in module_to_type:
                info["parent_type"] = module_to_type[pm]
            else:
                info["parent_type"] = None
    else:
        for info in type_to_module.values():
            info["parent_type"] = None

    return type_to_module


def _load_cache(kind: Kind) -> dict[str, Any] | None:
    """Load cache from disk if it exists and is valid JSON."""
    cache_file = _cache_file(kind)
    if not cache_file.exists():
        return None
    try:
        return json.loads(cache_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(kind: Kind, mtime: float, file_count: int, type_to_module: dict[str, dict[str, Any]]) -> None:
    """Save cache to disk."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "kind": kind,
            "root_mtime": mtime,
            "file_count": file_count,
            "type_to_module": type_to_module,
        }
        _cache_file(kind).write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_type_to_module_map(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    """Get the type -> module mapping for a kind, using cache if valid."""
    cached = _type_to_module[kind]
    if cached is not None:
        return cached

    root_dir = _root_dir(kind)
    current_mtime, current_count = _get_folder_fingerprint(root_dir)

    cache = _load_cache(kind)
    if cache is not None:
        cached_mtime = cache.get("root_mtime", 0)
        cached_count = cache.get("file_count", 0)
        if cached_mtime == current_mtime and cached_count == current_count:
            _type_to_module[kind] = cache["type_to_module"]
            return _type_to_module[kind]  # type: ignore[return-value]

    scanned = _scan_root(kind)
    _type_to_module[kind] = scanned
    _save_cache(kind, current_mtime, current_count, scanned)
    return scanned


def load_params_class(
    type_str: str, kind: Kind = "instrument", verbose: bool = False
) -> type:
    """Lazily load and cache a Params class by its type string for the given kind."""
    cache = _loaded_params[kind]
    if type_str in cache:
        if verbose:
            logger.debug(
                "[cache hit] %s '%s' -> %s",
                kind, type_str, cache[type_str].__name__,
            )
        return cache[type_str]

    type_map = get_type_to_module_map(kind)

    if type_str not in type_map:
        available = ", ".join(sorted(type_map.keys()))
        raise ValueError(
            f"Unknown {kind} type '{type_str}'. Available {kind} types: {available}"
        )

    info = type_map[type_str]
    if verbose:
        logger.debug(
            "[importing] %s '%s' from %s.%s",
            kind, type_str, info["module"], info["class_name"],
        )
    module = importlib.import_module(info["module"])
    cls = getattr(module, info["class_name"])
    cache[type_str] = cls
    return cls


def load_saver_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, kind="saver", verbose=verbose)


def load_plotter_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, kind="plotter", verbose=verbose)


def get_config_folder(params_cls: type) -> str | None:
    """Derive the config folder path from a Params class's module path.

    Examples:
        lab_wizard.lib.instruments.dbay.dbay -> "dbay"
        lab_wizard.lib.instruments.sim900.modules.sim928 -> "sim900/modules"
        lab_wizard.lib.instruments.general.prologix_gpib -> None (top-level)
    """
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


def clear_cache() -> None:
    """Clear in-memory and disk caches for ALL kinds."""
    for k in list(_KIND_SPECS):
        _type_to_module[k] = None
        _loaded_params[k] = {}
        _metadata_cache[k] = None
        cache_file = _cache_file(k)  # type: ignore[arg-type]
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass


def list_available_types(kind: Kind = "instrument") -> list[str]:
    """List all available type strings for a kind (sorted)."""
    return sorted(get_type_to_module_map(kind).keys())


# --------------- Rich metadata & parent-chain helpers ---------------


def get_parent_chain(type_str: str, kind: Kind = "instrument") -> list[str]:
    """Walk up the parent chain for a type. Returns [] for non-hierarchical kinds."""
    if not _KIND_SPECS[kind]["has_hierarchy"]:
        return []
    type_map = get_type_to_module_map(kind)
    chain: list[str] = []
    current = type_str
    seen: set[str] = set()
    while True:
        info = type_map.get(current)
        if info is None:
            break
        parent = info.get("parent_type")
        if not parent or parent in seen:
            break
        chain.append(parent)
        seen.add(parent)
        current = parent
    return chain


def get_metadata(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    """Return rich metadata for every discoverable type of the given kind.

    Each entry includes type, class_name, module, kind, is_top_level, is_child,
    parent_type, parent_chain, child_types, defaults, key_hint, discovery_actions.
    Non-hierarchical kinds (saver, plotter) always have parent_type=None,
    parent_chain=[], child_types=[], discovery_actions=[].
    """
    if _metadata_cache[kind] is not None:
        return _metadata_cache[kind]  # type: ignore[return-value]

    type_map = get_type_to_module_map(kind)
    has_hierarchy = _KIND_SPECS[kind]["has_hierarchy"]

    children_of: dict[str, list[str]] = {}
    if has_hierarchy:
        for ts, info in type_map.items():
            pt = info.get("parent_type")
            if pt:
                children_of.setdefault(pt, []).append(ts)

    result: dict[str, dict[str, Any]] = {}
    for ts, info in type_map.items():
        defaults: dict[str, Any] = {}
        key_hint: str | None = None
        discovery_actions: list[dict[str, Any]] = []
        try:
            cls = load_params_class(ts, kind=kind, verbose=False)
            defaults = cls().model_dump()
            key_hint = getattr(cls, "key_hint", None)
            if has_hierarchy and hasattr(cls, "discovery_actions"):
                discovery_actions = [
                    a.to_spec().model_dump() for a in cls.discovery_actions()
                ]
        except Exception:
            pass

        result[ts] = {
            "type": ts,
            "class_name": info["class_name"],
            "module": info["module"],
            "kind": kind,
            "is_top_level": info.get("is_top_level", True),
            "is_child": info.get("is_child", False),
            "parent_type": info.get("parent_type"),
            "parent_chain": get_parent_chain(ts, kind=kind),
            "child_types": sorted(children_of.get(ts, [])),
            "defaults": defaults,
            "key_hint": key_hint,
            "discovery_actions": discovery_actions,
        }

    _metadata_cache[kind] = result
    return result


def get_instrument_metadata() -> dict[str, dict[str, Any]]:
    """Backward-compatible wrapper around get_metadata("instrument")."""
    return get_metadata("instrument")


def get_saver_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("saver")


def get_plotter_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("plotter")
