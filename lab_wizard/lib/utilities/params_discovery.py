"""
Auto-discovery of Params classes from the instruments folder.

Uses a JSON cache for fast lookups, rebuilds cache only when folder changes.
This avoids the need for a manually maintained TYPE_REGISTRY.

Usage:
    from lab_wizard.lib.utilities.params_discovery import load_params_class, get_config_folder
    
    # Load a Params class by its type string
    params_cls = load_params_class("dbay")
    
    # Get the config folder for saving YAML
    folder = get_config_folder(params_cls)  # Returns "dbay" or None for top-level

    # Get rich metadata (parent chains, defaults, etc.) for all types
    meta = get_instrument_metadata()

    # Walk the parent chain for a child type
    chain = get_parent_chain("sim928")  # → ["sim900", "prologix_gpib"]
"""
from __future__ import annotations

import importlib
import json
import re
import logging
from pathlib import Path
from typing import Any


# Files/folders to skip during scanning (utilities, not instrument definitions)
SKIP_NAMES = {
    "__init__.py",
    "comm.py",
    "deps.py",
    "state.py",
    "addons",
    "__pycache__",
}

# Cache location
CACHE_DIR = Path.home() / ".cache" / "lab_wizard"
CACHE_FILE = CACHE_DIR / "params_cache.json"

# In-memory caches
_loaded_params: dict[str, type] = {}
_type_to_module: dict[str, dict[str, Any]] | None = None
_instrument_metadata: dict[str, dict[str, Any]] | None = None
logger = logging.getLogger("lab_wizard.lib.utilities.params_discovery")


def _get_instruments_dir() -> Path:
    """Get the instruments directory path."""
    return (Path(__file__).parent.parent / "instruments").resolve()


def _get_folder_fingerprint(instruments_dir: Path) -> tuple[float, int]:
    """Get (max_mtime, file_count) for cache invalidation."""
    max_mtime = instruments_dir.stat().st_mtime
    file_count = 0
    for py_file in instruments_dir.rglob("*.py"):
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
    path: Path, instruments_dir: Path
) -> list[dict[str, Any]]:
    """Scan a Python file for Params classes with type Literal fields.

    Returns list of dicts with keys:
        type_value, module, class_name, is_top_level, is_child, parent_module
    """
    results: list[dict[str, Any]] = []

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return results

    if "Params" not in content:
        return results

    # Find Params class definitions with their base classes
    class_iter = list(_CLASS_WITH_BASES.finditer(content))
    if not class_iter:
        return results

    try:
        rel_path = path.relative_to(instruments_dir)
        module_parts = ["lab_wizard", "lib", "instruments"] + list(
            rel_path.with_suffix("").parts
        )
        module_path = ".".join(module_parts)
    except ValueError:
        return results

    # Detect parent_class return values (on Child instrument classes in same file)
    parent_module: str | None = None
    parent_matches = _PARENT_CLASS_RETURN.findall(content)
    if parent_matches:
        raw = parent_matches[0]
        if not raw.startswith("lab_wizard."):
            raw = "lab_wizard." + raw
        # "lab_wizard.lib.instruments.sim900.sim900.Sim900" → module part
        parent_module = raw.rsplit(".", 1)[0]

    for i, match in enumerate(class_iter):
        class_name, bases_str = match.group(1), match.group(2)
        is_top_level = bool(re.search(r'\bCanInstantiate\b', bases_str))
        is_child = bool(re.search(r'\bChildParams\b', bases_str))
        # Only register classes that are actual instrument params
        if not is_top_level and not is_child:
            # could be a channel params class.
            continue
        # Search for type literal in THIS class's body only
        body_start = match.end()
        body_end = class_iter[i + 1].start() if i + 1 < len(class_iter) else len(content)
        class_body = content[body_start:body_end]
        type_match = _TYPE_LITERAL.search(class_body)
        if not type_match:
            continue  # No type literal → not a registered instrument
        results.append({
            "type_value": type_match.group(2),
            "module": module_path,
            "class_name": class_name,
            "is_top_level": is_top_level,
            "is_child": is_child,
            "parent_module": parent_module,
        })

    return results


def _scan_instruments_folder() -> dict[str, dict[str, Any]]:
    """Scan instruments folder for all Params classes.

    Returns mapping: type_string -> {module, class_name, is_top_level, is_child, parent_module}
    """
    instruments_dir = _get_instruments_dir()
    type_to_module: dict[str, dict[str, Any]] = {}

    for py_file in instruments_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue

        found = _scan_file_for_params(py_file, instruments_dir)

        for entry in found:
            tv = entry["type_value"]
            if tv in type_to_module:
                existing = type_to_module[tv]
                if existing["module"] != entry["module"] or existing["class_name"] != entry["class_name"]:
                    raise ValueError(
                        f"Duplicate type '{tv}' found in "
                        f"{existing['module']}.{existing['class_name']} and "
                        f"{entry['module']}.{entry['class_name']}"
                    )
            type_to_module[tv] = {
                "module": entry["module"],
                "class_name": entry["class_name"],
                "is_top_level": entry["is_top_level"],
                "is_child": entry["is_child"],
                "parent_module": entry["parent_module"],
            }

    # Resolve parent_module → parent_type using reverse lookup
    module_to_type: dict[str, str] = {
        v["module"]: k for k, v in type_to_module.items()
    }
    for info in type_to_module.values():
        pm = info.get("parent_module")
        if pm and pm in module_to_type:
            info["parent_type"] = module_to_type[pm]
        else:
            info["parent_type"] = None

    return type_to_module


def _load_cache() -> dict[str, Any] | None:
    """Load cache from disk if it exists and is valid JSON."""
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(mtime: float, file_count: int, type_to_module: dict[str, dict[str, Any]]) -> None:
    """Save cache to disk."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_data = {
            "instruments_mtime": mtime,
            "file_count": file_count,
            "type_to_module": type_to_module,
        }
        CACHE_FILE.write_text(json.dumps(cache_data, indent=2), encoding="utf-8")
    except OSError:
        pass


def get_type_to_module_map() -> dict[str, dict[str, Any]]:
    """Get the type -> module mapping, using cache if valid.

    Returns dict mapping type_string -> {
        "module": str, "class_name": str,
        "is_top_level": bool, "is_child": bool,
        "parent_type": str | None
    }
    """
    global _type_to_module

    if _type_to_module is not None:
        return _type_to_module

    instruments_dir = _get_instruments_dir()
    current_mtime, current_count = _get_folder_fingerprint(instruments_dir)

    cache = _load_cache()
    if cache is not None:
        cached_mtime = cache.get("instruments_mtime", 0)
        cached_count = cache.get("file_count", 0)
        if cached_mtime == current_mtime and cached_count == current_count:
            _type_to_module = cache["type_to_module"]
            return _type_to_module  # type: ignore[return-value]

    _type_to_module = _scan_instruments_folder()
    _save_cache(current_mtime, current_count, _type_to_module)
    return _type_to_module


def load_params_class(type_str: str, verbose: bool = False) -> type:
    """Lazily load and cache a Params class by its type string."""
    if type_str in _loaded_params:
        if verbose:
            logger.debug(
                "[cache hit] '%s' -> %s",
                type_str,
                _loaded_params[type_str].__name__,
            )
        return _loaded_params[type_str]

    type_map = get_type_to_module_map()

    if type_str not in type_map:
        available = ", ".join(sorted(type_map.keys()))
        raise ValueError(
            f"Unknown instrument type '{type_str}'. Available types: {available}"
        )

    info = type_map[type_str]

    if verbose:
        logger.debug(
            "[importing] '%s' from %s.%s",
            type_str,
            info["module"],
            info["class_name"],
        )

    module = importlib.import_module(info["module"])
    cls = getattr(module, info["class_name"])
    _loaded_params[type_str] = cls
    return cls


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
    """Clear both in-memory and disk caches."""
    global _type_to_module, _loaded_params, _instrument_metadata
    _type_to_module = None
    _loaded_params = {}
    _instrument_metadata = None
    if CACHE_FILE.exists():
        try:
            CACHE_FILE.unlink()
        except OSError:
            pass


def list_available_types() -> list[str]:
    """List all available instrument type strings (sorted)."""
    return sorted(get_type_to_module_map().keys())


# --------------- Rich metadata & parent-chain helpers ---------------


def get_parent_chain(type_str: str) -> list[str]:
    """Walk up the parent chain for a type.

    Returns list of ancestor type strings from immediate parent to root.
    Top-level instruments return [].

    Example: get_parent_chain("sim928") → ["sim900", "prologix_gpib"]
    """
    type_map = get_type_to_module_map()
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


def get_instrument_metadata() -> dict[str, dict[str, Any]]:
    """Return rich metadata for every discoverable instrument type.

    Each entry:
        {
            "type": str,
            "class_name": str,
            "module": str,
            "is_top_level": bool,
            "is_child": bool,
            "parent_type": str | None,
            "parent_chain": list[str],
            "child_types": list[str],
            "defaults": dict,
        }

    ``defaults`` is the model_dump() of a default-constructed Params instance.
    Types whose Params class requires non-default arguments will have
    defaults={} (construction failure is silently ignored).

    Result is cached in-process; call clear_cache() to invalidate.
    """
    global _instrument_metadata
    if _instrument_metadata is not None:
        return _instrument_metadata

    type_map = get_type_to_module_map()

    # Build child_types (inverse of parent_type)
    children_of: dict[str, list[str]] = {}
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
            cls = load_params_class(ts, verbose=False)
            defaults = cls().model_dump()
            key_hint = getattr(cls, "key_hint", None)
            if hasattr(cls, "discovery_actions"):
                discovery_actions = cls.discovery_actions()
        except Exception:
            pass

        result[ts] = {
            "type": ts,
            "class_name": info["class_name"],
            "module": info["module"],
            "is_top_level": info.get("is_top_level", False),
            "is_child": info.get("is_child", False),
            "parent_type": info.get("parent_type"),
            "parent_chain": get_parent_chain(ts),
            "child_types": sorted(children_of.get(ts, [])),
            "defaults": defaults,
            "key_hint": key_hint,
            "discovery_actions": discovery_actions,
        }

    _instrument_metadata = result
    return result

