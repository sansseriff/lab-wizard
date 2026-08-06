"""Fast, automatic resource indexing with runtime-validated metadata.

The source index is deliberately *not* a type system.  It uses :mod:`ast` to
locate Params candidates without importing driver modules, then records their
module and declared discriminator.  A runtime audit imports those candidates
and derives hierarchy and behavior from the real Python classes.  The audit's
JSON-safe result is persisted, so an unchanged wizard startup does not import
the complete instrument library merely to render resource choices.
"""

from __future__ import annotations

import ast
import hashlib
import importlib
import json
import os
from pathlib import Path
from types import UnionType
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from lab_wizard.lib.instruments.general.behavior import behavior_name_for
from lab_wizard.lib.instruments.general.parent_child import (
    CanInstantiate,
    ChannelProvider,
    ChildParams,
    ParentParams,
)


Kind = Literal["instrument", "saver", "plotter"]
SCHEMA_VERSION = 3

_KIND_DIRS = {
    "instrument": "instruments",
    "saver": "savers",
    "plotter": "plotters",
}
_SKIP_PARTS = {"__pycache__", "addons"}
_source_maps: dict[str, dict[str, dict[str, Any]] | None] = {
    kind: None for kind in _KIND_DIRS
}
_source_signatures: dict[str, str | None] = {kind: None for kind in _KIND_DIRS}
_source_fingerprints: dict[str, dict[str, dict[str, int]] | None] = {
    kind: None for kind in _KIND_DIRS
}
_loaded_params: dict[str, dict[str, type]] = {kind: {} for kind in _KIND_DIRS}
_stale_loaded: dict[str, set[str]] = {kind: set() for kind in _KIND_DIRS}
_metadata: dict[str, dict[str, dict[str, Any]] | None] = {
    kind: None for kind in _KIND_DIRS
}
_metadata_signatures: dict[str, str | None] = {kind: None for kind in _KIND_DIRS}


class ResourceAuditError(ValueError):
    """A source candidate failed validation as a runtime resource."""


def _lib_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _root_dir(kind: Kind) -> Path:
    return _lib_dir() / _KIND_DIRS[kind]


def _cache_dir() -> Path:
    override = os.environ.get("LAB_WIZARD_CACHE_DIR")
    return Path(override).expanduser() if override else Path.home() / ".cache" / "lab_wizard"


def _index_path(kind: Kind) -> Path:
    return _cache_dir() / f"resource_index_{kind}_v{SCHEMA_VERSION}.json"


def _metadata_path(kind: Kind) -> Path:
    return _cache_dir() / f"resource_metadata_{kind}_v{SCHEMA_VERSION}.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Best-effort atomic cache write; discovery remains usable if it fails."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def _files(kind: Kind) -> list[Path]:
    root = _root_dir(kind)
    if not root.exists():
        return []
    return sorted(
        path
        for path in root.rglob("*.py")
        if path.name != "__init__.py" and not (_SKIP_PARTS & set(path.parts))
    )


def _fingerprints(kind: Kind) -> dict[str, dict[str, int]]:
    root = _root_dir(kind)
    result: dict[str, dict[str, int]] = {}
    for path in _files(kind):
        try:
            stat = path.stat()
        except OSError:
            continue
        result[path.relative_to(root).as_posix()] = {
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
        }
    return result


def _literal_string(annotation: ast.expr) -> str | None:
    if not isinstance(annotation, ast.Subscript):
        return None
    target = annotation.value
    name = target.id if isinstance(target, ast.Name) else (
        target.attr if isinstance(target, ast.Attribute) else None
    )
    if name != "Literal":
        return None
    values = (
        annotation.slice.elts
        if isinstance(annotation.slice, ast.Tuple)
        else [annotation.slice]
    )
    if len(values) != 1:
        return None
    value = values[0]
    return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None


def _scan_file(path: Path, kind: Kind) -> list[dict[str, str]]:
    """Return cheap Params candidates; runtime audit makes the final decision."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError) as exc:
        raise ResourceAuditError(f"Could not index {path}: {exc}") from exc
    root = _root_dir(kind)
    relative = path.relative_to(root).with_suffix("")
    module = f"lab_wizard.lib.{_KIND_DIRS[kind]}." + ".".join(relative.parts)
    found: list[dict[str, str]] = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or not node.name.endswith("Params"):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.AnnAssign):
                continue
            if not isinstance(statement.target, ast.Name) or statement.target.id != "type":
                continue
            declared_type = _literal_string(statement.annotation)
            if declared_type is not None:
                found.append({
                    "type_value": declared_type,
                    "module": module,
                    "class_name": node.name,
                    "kind": kind,
                    "source_file": path.relative_to(root).as_posix(),
                })
            break
    return found


def _source_signature(fingerprints: dict[str, dict[str, int]]) -> str:
    payload = json.dumps(fingerprints, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_source_map(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    """Return the automatic type locator, rebuilding only after source changes."""
    fingerprints = _fingerprints(kind)
    signature = _source_signature(fingerprints)
    if _source_maps[kind] is not None and _source_signatures[kind] == signature:
        return _source_maps[kind]  # type: ignore[return-value]

    # Source changed underneath a long-lived backend. Locator and semantic
    # metadata must move together; already-imported live classes are left alone
    # and remain subject to the server's normal restart/release boundary.
    if _source_maps[kind] is not None:
        previous = _source_fingerprints[kind] or {}
        changed_files = {
            relative
            for relative in set(previous) | set(fingerprints)
            if previous.get(relative) != fingerprints.get(relative)
        }
        for type_str in _loaded_params[kind]:
            old_info = _source_maps[kind].get(type_str, {})
            if old_info.get("source_file") in changed_files:
                _stale_loaded[kind].add(type_str)
        _metadata[kind] = None
        _metadata_signatures[kind] = None
    cached = _read_json(_index_path(kind))
    if (
        cached is not None
        and cached.get("schema_version") == SCHEMA_VERSION
        and cached.get("fingerprints") == fingerprints
        and isinstance(cached.get("files"), dict)
        and isinstance(cached.get("type_to_module"), dict)
    ):
        result = cached["type_to_module"]
        _source_maps[kind] = result
        _source_signatures[kind] = signature
        _source_fingerprints[kind] = fingerprints
        return result

    cached_files = cached.get("files", {}) if cached is not None else {}
    indexed_files: dict[str, dict[str, Any]] = {}
    root = _root_dir(kind)
    for path in _files(kind):
        relative = path.relative_to(root).as_posix()
        old = cached_files.get(relative) if isinstance(cached_files, dict) else None
        if (
            isinstance(old, dict)
            and old.get("fingerprint") == fingerprints.get(relative)
            and isinstance(old.get("candidates"), list)
        ):
            candidates = old["candidates"]
        else:
            candidates = _scan_file(path, kind)
        indexed_files[relative] = {
            "fingerprint": fingerprints[relative],
            "candidates": candidates,
        }

    result: dict[str, dict[str, Any]] = {}
    for file_info in indexed_files.values():
        for raw_candidate in file_info["candidates"]:
            candidate = dict(raw_candidate)
            type_value = candidate.pop("type_value")
            existing = result.get(type_value)
            if existing is not None and existing != candidate:
                raise ResourceAuditError(
                    f"Duplicate {kind} type {type_value!r}: "
                    f"{existing['module']}.{existing['class_name']} and "
                    f"{candidate['module']}.{candidate['class_name']}"
                )
            result[type_value] = candidate

    _write_json(_index_path(kind), {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "fingerprints": fingerprints,
        "source_signature": _source_signature(fingerprints),
        "files": indexed_files,
        "type_to_module": result,
    })
    _source_maps[kind] = result
    _source_signatures[kind] = signature
    _source_fingerprints[kind] = fingerprints
    return result


def _runtime_base(kind: Kind) -> type:
    if kind == "instrument":
        return BaseModel
    if kind == "saver":
        from lab_wizard.lib.savers.base import SaverParams
        return SaverParams
    from lab_wizard.lib.plotters.base import PlotterParams
    return PlotterParams


def _validate_params_class(type_str: str, cls: type, kind: Kind) -> None:
    if not isinstance(cls, type) or not issubclass(cls, _runtime_base(kind)):
        raise ResourceAuditError(f"{cls!r} is not a valid {kind} Params class")
    if kind == "instrument" and not (
        issubclass(cls, CanInstantiate) or issubclass(cls, ChildParams)
    ):
        raise ResourceAuditError(
            f"{cls.__module__}.{cls.__name__} is neither CanInstantiate nor ChildParams"
        )
    field = getattr(cls, "model_fields", {}).get("type")
    values = get_args(field.annotation) if field is not None else ()
    if values != (type_str,) or field.default != type_str:
        raise ResourceAuditError(
            f"{cls.__module__}.{cls.__name__}.type must be "
            f"Literal[{type_str!r}] with the same default"
        )


def load_params_class(type_str: str, kind: Kind = "instrument", verbose: bool = False) -> type:
    """Lazily import and validate the Params class for ``type_str``."""
    del verbose  # compatibility with the old discovery facade
    loaded = _loaded_params[kind]
    if type_str in _stale_loaded[kind]:
        raise ResourceAuditError(
            f"{kind.title()} type {type_str!r} changed after its module was imported. "
            "Restart the process before rebuilding metadata or constructing it."
        )
    if type_str in loaded:
        return loaded[type_str]
    source_map = get_source_map(kind)
    if type_str not in source_map:
        # A long-lived process may have indexed the tree before a file appeared.
        _source_maps[kind] = None
        _source_signatures[kind] = None
        _source_fingerprints[kind] = None
        source_map = get_source_map(kind)
    if type_str not in source_map:
        available = ", ".join(sorted(source_map))
        raise ValueError(f"Unknown {kind} type {type_str!r}. Available {kind} types: {available}")
    info = source_map[type_str]
    try:
        module = importlib.import_module(info["module"])
        cls = getattr(module, info["class_name"])
        _validate_params_class(type_str, cls, kind)
    except Exception as exc:
        if isinstance(exc, ResourceAuditError):
            raise
        raise ResourceAuditError(
            f"Could not load {kind} type {type_str!r} from "
            f"{info['module']}.{info['class_name']}: {exc}"
        ) from exc
    loaded[type_str] = cls
    return cls


def _model_types(annotation: Any) -> set[type[BaseModel]]:
    """Extract concrete Pydantic model classes from a nested type annotation."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return {annotation}
    origin = get_origin(annotation)
    if origin in (dict, list, tuple, set, UnionType) or origin is not None:
        result: set[type[BaseModel]] = set()
        for argument in get_args(annotation):
            result.update(_model_types(argument))
        return result
    return set()


def _resource_class(params_cls: type) -> type | None:
    getter = getattr(params_cls, "resource_class", None)
    candidate = getter() if callable(getter) else None
    return candidate if isinstance(candidate, type) else None


def _channel_class(resource_cls: type | None) -> type | None:
    if resource_cls is None or not issubclass(resource_cls, ChannelProvider):
        return None
    candidate = getattr(resource_cls, "channel_class", None)
    return candidate if isinstance(candidate, type) else None


def _validate_resource_class(params_cls: type, resource_cls: type | None, kind: Kind) -> type:
    if resource_cls is None:
        raise ResourceAuditError(
            f"{params_cls.__module__}.{params_cls.__name__}.resource_class() "
            "did not return a class"
        )
    if kind == "instrument":
        from lab_wizard.lib.instruments.general.parent_child import Instrument

        expected = Instrument
    elif kind == "saver":
        from lab_wizard.lib.savers.saver import GenericSaver

        expected = GenericSaver
    else:
        from lab_wizard.lib.plotters.plotter import GenericPlotter

        expected = GenericPlotter
    if not issubclass(resource_cls, expected):
        raise ResourceAuditError(
            f"{params_cls.__module__}.{params_cls.__name__}.resource_class() returned "
            f"{resource_cls!r}, which is not a {expected.__name__} subclass"
        )
    if issubclass(resource_cls, ChannelProvider) and _channel_class(resource_cls) is None:
        raise ResourceAuditError(
            f"Channel provider {resource_cls.__module__}.{resource_cls.__name__} "
            "must declare channel_class"
        )
    return resource_cls


def _build_metadata(kind: Kind) -> dict[str, dict[str, Any]]:
    source_map = get_source_map(kind)
    classes = {type_str: load_params_class(type_str, kind) for type_str in source_map}

    parents: dict[str, str] = {}
    children_of: dict[str, list[str]] = {}
    if kind == "instrument":
        accepted: dict[str, set[type[BaseModel]]] = {}
        for parent_type, params_cls in classes.items():
            if not issubclass(params_cls, ParentParams):
                continue
            field = params_cls.model_fields.get("children")
            accepted[parent_type] = _model_types(field.annotation) if field else set()
        for child_type, child_cls in classes.items():
            if not issubclass(child_cls, ChildParams):
                continue
            matches = [
                parent_type
                for parent_type, accepted_types in accepted.items()
                if any(issubclass(child_cls, accepted_type) for accepted_type in accepted_types)
            ]
            if len(matches) != 1:
                raise ResourceAuditError(
                    f"Child Params {child_cls.__module__}.{child_cls.__name__} must match "
                    f"exactly one parent's children annotation; matched {matches}"
                )
            parents[child_type] = matches[0]
            children_of.setdefault(matches[0], []).append(child_type)

    def parent_chain(type_str: str) -> list[str]:
        chain: list[str] = []
        seen = {type_str}
        current = type_str
        while current in parents:
            current = parents[current]
            if current in seen:
                raise ResourceAuditError(f"Cycle in instrument parent graph at {current!r}")
            seen.add(current)
            chain.append(current)
        return chain

    result: dict[str, dict[str, Any]] = {}
    for type_str, params_cls in classes.items():
        info = source_map[type_str]
        default_params = params_cls()
        resource_cls = _validate_resource_class(params_cls, _resource_class(params_cls), kind)
        channel_cls = _channel_class(resource_cls)
        actions: list[dict[str, Any]] = []
        if kind == "instrument" and hasattr(params_cls, "discovery_actions"):
            actions = [action.to_spec().model_dump() for action in params_cls.discovery_actions()]
        result[type_str] = {
            "type": type_str,
            "class_name": info["class_name"],
            "module": info["module"],
            "kind": kind,
            "is_top_level": kind != "instrument" or issubclass(params_cls, CanInstantiate),
            "is_child": kind == "instrument" and issubclass(params_cls, ChildParams),
            "parent_type": parents.get(type_str),
            "parent_chain": parent_chain(type_str),
            "child_types": sorted(children_of.get(type_str, [])),
            "defaults": default_params.model_dump(),
            "key_hint": getattr(params_cls, "key_hint", None),
            "discovery_actions": actions,
            "behavior_abc": behavior_name_for(resource_cls, is_class=True),
            "channel_behavior_abc": (
                behavior_name_for(channel_cls, is_class=True) if channel_cls else None
            ),
            "resource_module": resource_cls.__module__ if resource_cls else None,
            "resource_class_name": resource_cls.__name__ if resource_cls else None,
            "channel_module": channel_cls.__module__ if channel_cls else None,
            "channel_class_name": channel_cls.__name__ if channel_cls else None,
        }
    return result


def get_metadata(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    """Return validated semantic metadata, reusing it for an unchanged source tree."""
    fingerprints = _fingerprints(kind)
    signature = _source_signature(fingerprints)
    if _metadata[kind] is not None and _metadata_signatures[kind] == signature:
        return _metadata[kind]  # type: ignore[return-value]
    _metadata[kind] = None
    cached = _read_json(_metadata_path(kind))
    if (
        cached is not None
        and cached.get("schema_version") == SCHEMA_VERSION
        and cached.get("source_signature") == signature
        and isinstance(cached.get("metadata"), dict)
    ):
        _metadata[kind] = cached["metadata"]
        _metadata_signatures[kind] = signature
        return _metadata[kind]  # type: ignore[return-value]
    result = _build_metadata(kind)
    _write_json(_metadata_path(kind), {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "source_signature": signature,
        "metadata": result,
    })
    _metadata[kind] = result
    _metadata_signatures[kind] = signature
    return result


def get_parent_chain(type_str: str, kind: Kind = "instrument") -> list[str]:
    info = get_metadata(kind).get(type_str)
    return list(info.get("parent_chain", [])) if info else []


def clear_cache() -> None:
    """Clear process state and both generations of generated cache files."""
    for kind in _KIND_DIRS:
        _source_maps[kind] = None
        _source_signatures[kind] = None
        _source_fingerprints[kind] = None
        _loaded_params[kind] = {}
        _stale_loaded[kind] = set()
        _metadata[kind] = None
        _metadata_signatures[kind] = None
        paths = [
            *_cache_dir().glob(f"resource_index_{kind}_v*.json"),
            *_cache_dir().glob(f"resource_metadata_{kind}_v*.json"),
            _cache_dir() / f"params_cache_{kind}.json",
        ]
        for path in paths:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def list_available_types(kind: Kind = "instrument") -> list[str]:
    return sorted(get_source_map(kind))


# Readable kind-specific entry points used by configuration and API layers.
def load_saver_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, "saver", verbose)


def load_plotter_params_class(type_str: str, verbose: bool = False) -> type:
    return load_params_class(type_str, "plotter", verbose)


def get_instrument_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("instrument")


def get_saver_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("saver")


def get_plotter_metadata() -> dict[str, dict[str, Any]]:
    return get_metadata("plotter")


def get_type_to_module_map(kind: Kind = "instrument") -> dict[str, dict[str, Any]]:
    return get_source_map(kind)
