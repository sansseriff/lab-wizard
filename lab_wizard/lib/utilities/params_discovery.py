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
"""
from __future__ import annotations

import importlib
import json
import re
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
_type_to_module: dict[str, dict[str, str]] | None = None


def _get_instruments_dir() -> Path:
    """Get the instruments directory path."""
    # Relative to this file: ../../instruments/
    return (Path(__file__).parent.parent / "instruments").resolve()


def _get_folder_fingerprint(instruments_dir: Path) -> tuple[float, int]:
    """
    Get folder fingerprint for cache invalidation.
    
    Returns (max_mtime, file_count) where max_mtime is the most recent
    modification time of any .py file in the tree.
    """
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


def _scan_file_for_params(path: Path, instruments_dir: Path) -> list[tuple[str, str, str]]:
    """
    Scan a Python file for Params classes with type Literal fields.
    
    Returns list of (type_value, module_path, class_name) tuples.
    """
    results: list[tuple[str, str, str]] = []
    
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return results
    
    # Quick check: does file contain "Params" at all?
    if "Params" not in content:
        return results
    
    # Find class definitions ending in Params
    class_pattern = re.compile(r'class\s+(\w+Params)\s*\(')
    class_matches = class_pattern.findall(content)
    
    if not class_matches:
        return results
    
    # Find type Literal assignments: type: Literal["something"] = "something"
    # This pattern handles both single and double quotes
    type_pattern = re.compile(r'type:\s*Literal\[(["\'])([^"\']+)\1\]')
    type_match = type_pattern.search(content)
    
    if not type_match:
        return results
    
    type_value = type_match.group(2)
    
    # Convert path to module path
    # instruments_dir is like .../lab_wizard/lib/instruments
    # We need the path relative to the package root (lab_wizard)
    try:
        # Get path relative to instruments dir, then build full module path
        rel_path = path.relative_to(instruments_dir)
        module_parts = ["lab_wizard", "lib", "instruments"] + list(rel_path.with_suffix("").parts)
        module_path = ".".join(module_parts)
        
        # Return all Params classes found (usually just one per file)
        for class_name in class_matches:
            results.append((type_value, module_path, class_name))
    except ValueError:
        # Path not relative to instruments_dir
        pass
    
    return results


def _scan_instruments_folder() -> dict[str, dict[str, str]]:
    """
    Scan instruments folder for all Params classes.
    
    Returns mapping: type_string -> {"module": module_path, "class_name": class_name}
    """
    instruments_dir = _get_instruments_dir()
    type_to_module: dict[str, dict[str, str]] = {}
    
    for py_file in instruments_dir.rglob("*.py"):
        if _should_skip(py_file):
            continue
        
        found = _scan_file_for_params(py_file, instruments_dir)
        
        for type_value, module_path, class_name in found:
            if type_value in type_to_module:
                existing = type_to_module[type_value]
                # Only warn if it's actually a different class
                if existing["module"] != module_path or existing["class_name"] != class_name:
                    raise ValueError(
                        f"Duplicate type '{type_value}' found in "
                        f"{existing['module']}.{existing['class_name']} and "
                        f"{module_path}.{class_name}"
                    )
            type_to_module[type_value] = {
                "module": module_path,
                "class_name": class_name,
            }
    
    return type_to_module


def _load_cache() -> dict[str, Any] | None:
    """Load cache from disk if it exists and is valid JSON."""
    if not CACHE_FILE.exists():
        return None
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(mtime: float, file_count: int, type_to_module: dict[str, dict[str, str]]) -> None:
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
        # Cache write failed - not critical, just continue without caching
        pass


def get_type_to_module_map() -> dict[str, dict[str, str]]:
    """
    Get the type -> module mapping, using cache if valid.
    
    This is the main entry point for discovery. It:
    1. Checks if cache exists and is still valid (folder unchanged)
    2. If valid, returns cached mapping
    3. If invalid/missing, scans folder and updates cache
    
    Returns:
        Dict mapping type_string -> {"module": str, "class_name": str}
    """
    global _type_to_module
    
    # Return in-memory cache if available
    if _type_to_module is not None:
        return _type_to_module
    
    instruments_dir = _get_instruments_dir()
    current_mtime, current_count = _get_folder_fingerprint(instruments_dir)
    
    # Try to use disk cache
    cache = _load_cache()
    if cache is not None:
        cached_mtime = cache.get("instruments_mtime", 0)
        cached_count = cache.get("file_count", 0)
        
        # Cache is valid if mtime and file count match
        if cached_mtime == current_mtime and cached_count == current_count:
            _type_to_module = cache["type_to_module"]
            return _type_to_module  # type: ignore[return-value]
    
    # Cache invalid or missing - rescan folder
    _type_to_module = _scan_instruments_folder()
    _save_cache(current_mtime, current_count, _type_to_module)
    
    return _type_to_module


def load_params_class(type_str: str, verbose: bool = True) -> type:
    """
    Lazily load and cache a Params class by its type string.
    
    Args:
        type_str: The type discriminator value (e.g., "dbay", "sim900")
        verbose: If True, print info about imports (default True)
        
    Returns:
        The Params class (e.g., DBayParams, Sim900Params)
        
    Raises:
        ValueError: If type_str is not found in the instruments folder
    """
    # Check in-memory cache first
    if type_str in _loaded_params:
        if verbose:
            print(f"  [cache hit] '{type_str}' -> {_loaded_params[type_str].__name__}")
        return _loaded_params[type_str]
    
    type_map = get_type_to_module_map()
    
    if type_str not in type_map:
        available = ", ".join(sorted(type_map.keys()))
        raise ValueError(
            f"Unknown instrument type '{type_str}'. "
            f"Available types: {available}"
        )
    
    info = type_map[type_str]
    
    if verbose:
        print(f"  [importing] '{type_str}' from {info['module']}.{info['class_name']}")
    
    module = importlib.import_module(info["module"])
    cls = getattr(module, info["class_name"])
    
    # Cache the loaded class
    _loaded_params[type_str] = cls
    
    return cls


def get_config_folder(params_cls: type) -> str | None:
    """
    Derive the config folder path from a Params class's module path.
    
    This determines where YAML files for this instrument type should be saved
    under config/instruments/.
    
    Examples:
        - lab_wizard.lib.instruments.dbay.dbay.DBayParams -> "dbay"
        - lab_wizard.lib.instruments.sim900.modules.sim928.Sim928Params -> "sim900/modules"
        - lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIBParams -> None (top-level)
    
    Args:
        params_cls: A Params class
        
    Returns:
        Folder path relative to config/instruments/, or None for top-level
    """
    module = params_cls.__module__
    
    # Extract the part after "lab_wizard.lib.instruments."
    prefix = "lab_wizard.lib.instruments."
    if not module.startswith(prefix):
        return None
    
    suffix = module[len(prefix):]
    parts = suffix.split(".")
    
    # Instruments in "general/" are top-level (go directly in instruments/)
    if parts[0] == "general":
        return None
    
    # Drop the filename (last part), keep folder structure
    folder_parts = parts[:-1]
    return "/".join(folder_parts) if folder_parts else None


def clear_cache() -> None:
    """
    Clear both in-memory and disk caches.
    
    Useful for testing or forcing a rescan.
    """
    global _type_to_module, _loaded_params
    _type_to_module = None
    _loaded_params = {}
    
    if CACHE_FILE.exists():
        try:
            CACHE_FILE.unlink()
        except OSError:
            pass


def list_available_types() -> list[str]:
    """
    List all available instrument type strings.
    
    Returns:
        Sorted list of type strings (e.g., ["dbay", "dac4D", "sim900", ...])
    """
    return sorted(get_type_to_module_map().keys())

