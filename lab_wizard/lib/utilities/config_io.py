from __future__ import annotations

"""
Config I/O utilities for loading, merging, and saving the multi-file instruments config
into a single in-memory pydantic Params tree rooted at instruments.

Directory layout — each parent's YAML and its children's folder share the same slug name:

config/
  instruments/
    prologix_gpib_key_<slug(port)>.yml
    prologix_gpib_key_<slug(port)>/
      sim900_key_<gpib>.yml
      sim900_key_<gpib>/
        sim928_key_<slot>.yml
        sim970_key_<slot>.yml
    dbay_key_<slug(ip:port)>.yml
    dbay_key_<slug(ip:port)>/
      dac4D_key_<slot>.yml
      dac16D_key_<slot>.yml
    keysight53220A_key_<slug(ip:port)>.yml

This structure guarantees no key collisions even when multiple instances of the same
parent type exist (e.g. two sim900 racks both containing a sim928 at slot 1).

Notes:
- Parents express children as a mapping from string keys to refs: {kind, ref}.
- All top-level instrument YAMLs live directly under instruments/ (no type-named
  subfolders). Children are nested under a folder named after their parent's YAML stem.
- Leaf Params may carry an 'attribute_name' string as a user-facing identifier.

Provided functions:
- load_instruments(config_dir) -> dict[str, InstrumentParams]
- merge_parent_params(base, delta) -> base (mutated) for tree-union semantics
- merge_instruments(base_dict, delta_dict) -> merged dict (mutates base)
- save_instruments_to_config(instruments, config_dir) -> writes files back using stable paths
- load_merge_save_instruments(config_dir, subset_instruments) -> merged dict
"""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List, cast, Iterable
import hashlib
import re
import logging

from pydantic import BaseModel
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq

# Auto-discovery of Params classes (replaces manual TYPE_REGISTRY)
from lab_wizard.lib.utilities.params_discovery import load_params_class

# KeyLike mixins — used for generic key derivation without hardcoding type strings
from lab_wizard.lib.instruments.general.parent_child import USBLike, IPLike, SlotLike, GPIBAddressLike

logger = logging.getLogger("lab_wizard.lib.utilities.config_io")


# ---------------------------- YAML helpers ----------------------------

# Use round-trip mode so we can preserve and add helpful comments in the
# on-disk config tree, while still converting to plain dicts before
# instantiating Pydantic models.
_yaml: Any = YAML(typ="rt")
_yaml.default_flow_style = False


def _read_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        loaded: Any = _yaml.load(f)
        return cast(Dict[str, Any], loaded or {})


def _write_yaml(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Convert plain dicts into a CommentedMap so we can attach comments such
    # as "managed by wizard" to specific keys (e.g. child refs).
    if isinstance(data, CommentedMap):
        data_to_dump: Any = data
    elif isinstance(data, dict):
        data_to_dump = to_commented_yaml_value(data)
    else:
        data_to_dump = data
    with path.open("w", encoding="utf-8") as f:
        _yaml.dump(data_to_dump, f)


def _field_description(model: BaseModel, field_name: str) -> str | None:
    field = type(model).model_fields.get(field_name)
    if field is None:
        return None
    desc = field.description
    if not desc:
        return None
    return desc.strip() or None


def to_commented_yaml_value(value: Any) -> Any:
    """Convert nested Python/Pydantic values into ruamel commented containers."""
    if isinstance(value, (CommentedMap, CommentedSeq)):
        return value
    if isinstance(value, BaseModel):
        return model_to_commented_map(value)
    if isinstance(value, dict):
        cm = CommentedMap()
        dict_value = cast(dict[Any, Any], value)
        for k, v in dict_value.items():
            cm[k] = to_commented_yaml_value(v)
        return cm
    if isinstance(value, list):
        seq = CommentedSeq()
        seq_any: Any = seq
        list_value = cast(list[Any], value)
        for item in list_value:
            seq_any.append(to_commented_yaml_value(item))
        return seq
    return value


def _yaml_priority_fields(model_cls: type) -> list[str]:
    """Collect YAML-priority fields from a model class via MRO.

    Always puts 'type' first (if present), then collects _yaml_key_fields_
    from each base class in MRO order (deduplicating). This means adding
    _yaml_key_fields_ to a new XXXLike mixin automatically floats its fields
    to the top without any changes here.
    """
    seen: set[str] = set()
    result: list[str] = []
    all_fields = set(model_cls.model_fields)
    if "type" in all_fields:
        result.append("type")
        seen.add("type")
    for base in model_cls.__mro__:
        for fname in getattr(base, "_yaml_key_fields_", ()):
            if fname not in seen and fname in all_fields:
                seen.add(fname)
                result.append(fname)
    return result


def model_to_commented_map(
    model: BaseModel,
    *,
    exclude_none: bool = False,
    exclude_fields: Iterable[str] = (),
    drop_enabled_true: bool = False,
) -> CommentedMap:
    """Build a CommentedMap from a Pydantic model, attaching Field descriptions."""
    excluded = set(exclude_fields)
    priority = _yaml_priority_fields(type(model))
    rest = [f for f in type(model).model_fields if f not in set(priority)]
    cm = CommentedMap()
    for field_name in priority + rest:
        if field_name in excluded:
            continue
        field_value = getattr(model, field_name)
        if exclude_none and field_value is None:
            continue
        if drop_enabled_true and field_name == "enabled" and field_value is True:
            continue
        cm[field_name] = to_commented_yaml_value(field_value)
        description = _field_description(model, field_name)
        if description:
            cm.yaml_add_eol_comment(  # type: ignore[no-untyped-call]
                description, key=field_name
            )
    return cm


# ---------------------------- Hash key helpers ----------------------------


_HASH_KEY_RE = re.compile(r"^[0-9a-f]{8}$")


def _is_hash_key(key: str) -> bool:
    """Return True if ``key`` looks like an 8-char hex hash (not a raw address)."""
    return bool(_HASH_KEY_RE.match(key))


def instrument_hash(type_str: str, key_value: str) -> str:
    """Compute an 8-char deterministic hex hash from instrument type + addressing value.

    This is used as the dict key / filename component for instruments, replacing
    raw address strings (port paths, IP:port, slot numbers, GPIB addresses).
    The hash is stable: the same type + key_value always produces the same result.
    """
    raw = f"{type_str}:{key_value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:8]


# ---------------------------- Loading ----------------------------

def _resolve_child_file(base_dir: Path, ref: str) -> Path:
    # ref is relative to instruments dir
    return (base_dir / "instruments" / ref).resolve()


def _load_node(
    base_dir: Path,
    node_path: Path,
    visited_paths: Optional[set[Path]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Load a node YAML file and return (Params instance, raw dict).

    Does not load children yet. When visited_paths is provided, the resolved
    node_path is added to that set so callers can reconstruct the set of all
    YAML files that participate in the logical instruments tree.
    """
    if visited_paths is not None:
        visited_paths.add(node_path.resolve())
    data = _read_yaml(node_path)
    type_str = data.get("type")
    if not isinstance(type_str, str):
        raise ValueError(f"Missing/invalid 'type' in {node_path}")
    # Copy without children for instantiation; children processed separately
    shallow = {k: v for k, v in data.items() if k != "children"}
    # Use auto-discovery to load the Params class
    params_cls = load_params_class(type_str)
    params = params_cls(**shallow)
    return params, data


def _attach_children(
    base_dir: Path,
    parent_params: Any,
    raw_dict: Dict[str, Any],
    visited_paths: Optional[set[Path]] = None,
) -> None:
    # Children are represented in YAML as a mapping: key -> {kind, ref}
    children_map: Dict[str, Dict[str, Any]] = cast(
        Dict[str, Dict[str, Any]], raw_dict.get("children") or {}
    )
    if not children_map:
        return
    for key, entry in list(children_map.items()):
        kind = cast(Optional[str], entry.get("kind"))
        ref = cast(Optional[str], entry.get("ref"))
        if not (isinstance(kind, str) and isinstance(ref, str)):
            raise ValueError("child entry requires string fields: kind, ref, and mapping key")
        child_path = _resolve_child_file(base_dir, ref)
        child_params, child_raw = _load_node(base_dir, child_path, visited_paths)

        if getattr(child_params, "enabled", True) is False:
            continue

        # Migration: if the YAML key looks like a raw address (not a hash) and
        # the child params has an apply_key() method, inject the raw key value
        # into the params field (e.g. set gpib_address='4' or slot='1').
        # This handles one-time migration from old format where the dict key WAS
        # the address.  Once normalize_instruments re-saves, the YAML will carry
        # the address as a params field and the key will be a hash.
        if not _is_hash_key(key) and hasattr(child_params, "apply_key"):
            child_params.apply_key(key)

        # recursively attach grandchildren
        _attach_children(base_dir, child_params, child_raw, visited_paths)
        # add to parent — use the hash key derived from child params, not the
        # raw YAML key, so the in-memory tree already has canonical keys.
        if not hasattr(parent_params, "children"):
            raise ValueError(f"Parent type {type(parent_params).__name__} has no children field")
        child_type_str = str(getattr(child_params, "type", ""))
        child_key = _key_for_loaded_params(child_params, child_type_str)
        parent_params.children[child_key] = child_params  # type: ignore[attr-defined]


def _key_for_loaded_params(params: Any, type_str: str) -> str:
    """Derive the in-memory dict key for a loaded Params object.

    If the params object has a ``key_fields()`` method (i.e. inherits from any
    KeyLike mixin — USBLike, IPLike, SlotLike, GPIBAddressLike), the key is
    computed as ``instrument_hash(type_str, key_fields())``.  This keeps raw
    hardware addresses out of generated Python files and makes keys stable
    under filesystem-safe naming.  Falls back to the type string for params
    that carry no addressing information.
    """
    if hasattr(params, "key_fields"):
        raw = params.key_fields()
        return instrument_hash(type_str, raw) if raw else type_str
    return type_str


def load_instruments(
    config_dir: str | Path,
    visited_paths: Optional[set[Path]] = None,
) -> Dict[str, Any]:
    """Load the instruments config into a top-level instruments dict mapping keys to Params.

    All top-level instrument YAMLs live directly under instruments/*.yml.
    Their in-memory key is derived from the KeyLike mixin when available
    (USBLike → port, IPLike → ip_address:ip_port) or falls back to the type string.
    """
    base_dir = Path(config_dir)
    inst_dir = (base_dir / "instruments").resolve()
    instruments: Dict[str, Any] = {}

    if not inst_dir.exists():
        return instruments

    for p in sorted(inst_dir.glob("*.yml")):
        params, raw = _load_node(base_dir, p, visited_paths)

        if getattr(params, "enabled", True) is False:
            continue

        _attach_children(base_dir, params, raw, visited_paths)
        type_str = str(raw.get("type") or "")
        key = _key_for_loaded_params(params, type_str)
        instruments[key] = params

    logger.debug("Loaded %d top-level instruments from %s", len(instruments), inst_dir)
    return instruments


def load_instruments_with_paths(
    config_dir: str | Path,
) -> Tuple[Dict[str, Any], set[Path]]:
    """Load instruments and also return the set of YAML files that participated.

    This is useful for normalization / cleanup tooling that wants to know which
    files are reachable from the logical instruments tree.
    """

    visited: set[Path] = set()
    instruments = load_instruments(config_dir, visited_paths=visited)
    return instruments, visited


# ---------------------------- Merging ----------------------------

def merge_parent_params(base_parent: Any, delta_parent: Any) -> Any:
    """Merge delta_parent into base_parent in-place, unioning children by key.

    - For simple fields present in delta (excluding children), overwrite base fields.
    - For children dicts, recursively merge when both are parents; otherwise replace child.
    """
    # Merge simple fields
    for name in getattr(base_parent, "model_fields", {}).keys():  # pydantic v2
        if name == "children":
            continue
        if hasattr(delta_parent, name):
            setattr(base_parent, name, getattr(delta_parent, name))

    # Merge children
    base_children: Dict[str, Any] = getattr(base_parent, "children", {})
    delta_children: Dict[str, Any] = getattr(delta_parent, "children", {})
    for key, dchild in (delta_children or {}).items():
        if key not in base_children:
            base_children[key] = dchild
            continue
        bchild = base_children[key]
        # If both have children attribute, treat as parent and merge recursively
        if hasattr(bchild, "children") and hasattr(dchild, "children"):
            merge_parent_params(bchild, dchild)
        else:
            base_children[key] = dchild
    # write back possibly new dict
    if hasattr(base_parent, "children"):
        base_parent.children = base_children  # type: ignore[attr-defined]
    return base_parent


# ---------------------------- Saving ----------------------------

def _node_filename(type_str: str, key: Optional[str]) -> str:
    """Return the .yml filename for a node based on type and key.

    Keys are now 8-char hex hashes, which are already filesystem-safe — no
    slug encoding needed.
    """
    if key:
        return f"{type_str}_key_{key}.yml"
    return f"{type_str}.yml"


def _dump_parent_to_dict(params: Any, child_refs: Dict[str, Dict[str, str]]) -> CommentedMap:
    if not isinstance(params, BaseModel):
        raise TypeError(f"Expected BaseModel params, got {type(params).__name__}")
    cm = model_to_commented_map(
        params,
        exclude_none=False,
        exclude_fields=("children",),
        drop_enabled_true=True,
    )
    if child_refs:
        cm["children"] = to_commented_yaml_value(child_refs)
    return cm


def _collect_written_paths(
    inst_dir: Path,
    parent_dir: Path,
    params: Any,
    here_key: Optional[str],
    out: set[Path],
) -> None:
    """Compute the set of file paths that _save_node_recursive WOULD write for params tree."""
    type_str: str = getattr(params, "type")
    fname = _node_filename(type_str, here_key)
    target = parent_dir / fname
    out.add(target)
    child_dir = parent_dir / fname[:-4]  # strip .yml → sibling children folder
    for key, child in (getattr(params, "children", {}) or {}).items():
        _collect_written_paths(inst_dir, child_dir, child, key, out)


def _save_node_recursive(
    inst_dir: Path,
    parent_dir: Path,
    params: Any,
    here_key: Optional[str],
) -> Tuple[Path, CommentedMap]:
    """Recursively write params and its children to YAML files.

    inst_dir  — root instruments/ directory, used to compute ref strings.
    parent_dir — directory into which THIS node's .yml is written.
    Children are written into a sibling folder: parent_dir / <this_node_stem>/
    """
    type_str: str = getattr(params, "type")
    fname = _node_filename(type_str, here_key)
    target = parent_dir / fname
    child_dir = parent_dir / fname[:-4]  # strip .yml → children folder

    # Save children first so we can record their refs in the parent YAML.
    child_refs: Dict[str, Dict[str, str]] = {}
    for key, child in (getattr(params, "children", {}) or {}).items():
        c_type = getattr(child, "type")
        c_path, _ = _save_node_recursive(inst_dir, child_dir, child, key)
        # ref is relative to instruments/ so the YAML is portable
        ref = c_path.relative_to(inst_dir).as_posix()
        child_refs[str(key)] = {"kind": str(c_type), "ref": ref}

    cm = _dump_parent_to_dict(params, child_refs)
    _write_yaml(target, cm)
    return target, cm


def save_instruments_to_config(instruments: Dict[str, Any], config_dir: str | Path) -> None:
    """Write the given instruments dict into config/instruments as multi-file YAML."""
    base_dir = Path(config_dir)
    inst_dir = (base_dir / "instruments").resolve()
    for key, params in (instruments or {}).items():
        _save_node_recursive(inst_dir, inst_dir, params, here_key=str(key))
    logger.info("Saved %d top-level instruments to %s", len(instruments), inst_dir)


# ---------------------------- Hash validation ----------------------------


def validate_and_repair_hashes(
    instruments: Dict[str, Any],
) -> Tuple[Dict[str, Any], bool]:
    """Walk the instruments tree; rekey any node whose hash no longer matches its params.

    This is called automatically after loading a project YAML. If a user edits
    a param field that feeds into the key (e.g. changes ``slot`` from ``'1'``
    to ``'2'``), the stale hash key is replaced with the correct one on the
    next load and the YAML is rewritten in-place.

    Returns:
        (repaired_dict, was_changed) — repaired_dict is a new dict with correct
        keys; was_changed is True if any key was updated.
    """
    changed = False
    repaired: Dict[str, Any] = {}
    for old_key, params in instruments.items():
        type_str = str(getattr(params, "type", ""))
        expected_key = _key_for_loaded_params(params, type_str)
        if old_key != expected_key:
            logger.info(
                "Hash mismatch for %s: old key=%r → new key=%r (params changed)",
                type_str,
                old_key,
                expected_key,
            )
            changed = True
        # Recurse into children
        if hasattr(params, "children") and params.children:
            repaired_children, child_changed = validate_and_repair_hashes(params.children)
            if child_changed:
                params.children = repaired_children  # type: ignore[attr-defined]
                changed = True
        repaired[expected_key] = params
    return repaired, changed


# ---------------------------- High-level workflows ----------------------------

def merge_instruments(base: Dict[str, Any], delta: Dict[str, Any]) -> Dict[str, Any]:
    """Merge delta instruments dict into base dict (in place)."""
    for key, dval in (delta or {}).items():
        if key not in base:
            base[key] = dval
            continue
        bval = base[key]
        # If both look like parents (have children), deep-merge
        if hasattr(bval, "children") and hasattr(dval, "children"):
            merge_parent_params(bval, dval)
        else:
            base[key] = dval
    return base


def load_merge_save_instruments(config_dir: str | Path, subset: Dict[str, Any]) -> Dict[str, Any]:
    """Load current instruments, merge the provided subset dict, and persist the result.

    Returns the merged instruments dict.
    """
    full = load_instruments(config_dir)
    merged = merge_instruments(full, subset)
    save_instruments_to_config(merged, config_dir)
    return merged


# ---------------------------- Normalization ----------------------------

def _iter_instrument_yaml_files(config_dir: Path) -> List[Path]:
    """Return a list of all YAML files under config/instruments.

    Used by normalization tooling to detect orphaned files.
    """

    inst_dir = (config_dir / "instruments").resolve()
    if not inst_dir.exists():
        return []
    return sorted(inst_dir.rglob("*.yml"))


def normalize_instruments(config_dir: str | Path) -> Dict[str, Any]:
    """Normalize the instruments config tree.

    Operations:
    - Load the current instruments tree (hash keys are recomputed from params).
    - Compute the canonical set of files that the tree WILL produce on save.
    - Save using canonical hash-based filenames.
    - Remove any YAML files under config/instruments that are no longer part of
      the canonical set (e.g. old slug-named files from before the hash migration,
      or orphans from type/key renames).

    This is safe to run multiple times; subsequent calls are idempotent.
    """

    base_dir = Path(config_dir)
    inst_dir = (base_dir / "instruments").resolve()

    # Step 1: Load current tree (keys are recomputed as hashes from params).
    instruments = load_instruments(config_dir)

    # Step 2: Compute which files the CURRENT tree will produce after saving.
    # We do this BEFORE writing so we can diff against what is currently on disk.
    files_to_keep: set[Path] = set()
    for k, v in instruments.items():
        _collect_written_paths(inst_dir, inst_dir, v, str(k), files_to_keep)

    # Step 3: Save with canonical hash-based filenames.
    save_instruments_to_config(instruments, config_dir)

    # Step 4: Remove any YAML files not in the canonical set
    # (old slug-encoded names, orphans, etc.).
    for f in _iter_instrument_yaml_files(base_dir):
        if f not in files_to_keep:
            try:
                f.unlink()
            except OSError:
                pass

    # Step 5: Remove any directories that are now empty (deepest first).
    for d in sorted(inst_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    return instruments


# -------------------- Manage Instruments CRUD --------------------


def _default_key_for_params(type_str: str, params: Any) -> str:
    """Derive a sensible dict key from a Params instance's default values.

    Uses the KeyLike mixin when available; falls back to the type string.
    """
    return _key_for_loaded_params(params, type_str)


def _apply_key_to_params(type_str: str, params: Any, key: str) -> None:
    """Set the identifying param fields to match the user-provided key.

    This ensures the YAML content stays consistent with the filename/key
    so that load_instruments() won't produce key collisions.

    For top-level instruments the key is expected to be a raw address value
    (e.g. ``/dev/ttyUSB0``, ``10.7.0.3:8888``) that will be hashed by
    _key_for_loaded_params when the tree is re-loaded.
    """
    if hasattr(params, "apply_key"):
        params.apply_key(key)


def _node_to_tree_dict(key: str, params: Any) -> Dict[str, Any]:
    """Recursively serialize a Params tree node into a JSON-friendly dict."""
    type_str = str(getattr(params, "type", ""))
    fields = params.model_dump()
    fields.pop("children", None)
    children_dict: Dict[str, Any] = {}
    for ck, cp in (getattr(params, "children", {}) or {}).items():
        children_dict[ck] = _node_to_tree_dict(ck, cp)
    return {
        "type": type_str,
        "key": key,
        "fields": fields,
        "children": children_dict,
    }


def get_configured_tree(config_dir: str | Path) -> List[Dict[str, Any]]:
    """Load instruments and return a JSON-serializable tree for the frontend.

    Returns a list of top-level instrument dicts, each with nested children.
    """
    instruments = load_instruments(config_dir)
    return [_node_to_tree_dict(k, v) for k, v in instruments.items()]


def initialize_instrument(
    config_dir: str | Path,
    type_str: str,
    key: Optional[str] = None,
) -> Path:
    """Create a default YAML config for a top-level instrument from its Params class.

    Returns the path to the written YAML file.
    """
    params_cls = load_params_class(type_str)
    params = params_cls()
    if key is None:
        key = _default_key_for_params(type_str, params)
    else:
        _apply_key_to_params(type_str, params, key)
    inst_dir = (Path(config_dir) / "instruments").resolve()
    target, _ = _save_node_recursive(inst_dir, inst_dir, params, here_key=key)
    logger.info("Initialized instrument type=%s key=%s at %s", type_str, key, target)
    return target


def add_instrument_chain(
    config_dir: str | Path,
    chain: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Process a chain of operations to add an instrument (possibly with new parents).

    ``chain`` is ordered leaf-first, e.g.:
        [
            {"type": "sim928", "key": "1", "action": "create_new"},
            {"type": "sim900", "key": "7", "action": "create_new"},
            {"type": "prologix_gpib", "key": "/dev/ttyUSB0", "action": "use_existing"},
        ]

    Returns dict with status and updated tree.
    """
    instruments = load_instruments(config_dir)

    # Process top-down (reverse the chain: root ancestor first)
    steps = list(reversed(chain))

    current_parent: Any = None
    for step in steps:
        ts = step["type"]
        key = step["key"]
        action = step["action"]

        if action == "use_existing":
            if current_parent is None:
                if key not in instruments:
                    raise ValueError(
                        f"Cannot find existing top-level instrument with key '{key}' "
                        f"(type={ts}). Available: {list(instruments.keys())}"
                    )
                current_parent = instruments[key]
            else:
                children = getattr(current_parent, "children", {})
                if key not in children:
                    raise ValueError(
                        f"Cannot find existing child with key '{key}' "
                        f"(type={ts}) under parent"
                    )
                current_parent = children[key]

        elif action == "create_new":
            params_cls = load_params_class(ts)
            new_params = params_cls()
            _apply_key_to_params(ts, new_params, key)

            # Always use the hash key for storage so filenames are filesystem-safe.
            # The raw key value was already applied to the params fields above via
            # apply_key(), so the hardware address is preserved in the YAML content.
            hash_key = _key_for_loaded_params(new_params, ts)

            if current_parent is None:
                instruments[hash_key] = new_params
                current_parent = new_params
            else:
                if not hasattr(current_parent, "children"):
                    raise ValueError(
                        f"Parent type {type(current_parent).__name__} does not support children"
                    )
                current_parent.children[hash_key] = new_params  # type: ignore[attr-defined]
                current_parent = new_params

    save_instruments_to_config(instruments, config_dir)
    logger.info("Added/updated instrument chain with %d steps", len(chain))
    return {"status": "ok", "tree": get_configured_tree(config_dir)}


def reinitialize_instrument(
    config_dir: str | Path,
    type_str: str,
    key: str,
) -> Dict[str, Any]:
    """Reset an instrument's config to default values, preserving children.

    Finds the instrument in the tree by type + key, replaces its fields with
    defaults from the Params class, keeps existing children, and saves.
    """
    instruments = load_instruments(config_dir)
    params_cls = load_params_class(type_str)
    default_params = params_cls()

    target = _find_node_in_tree(instruments, type_str, key)
    if target is None:
        raise ValueError(f"Instrument type={type_str} key={key} not found in config")

    # Preserve the key-field value (port/slot/gpib_address etc.) before reset so
    # the instrument's hardware address — and therefore its hash key — is unchanged.
    key_field_value: Optional[str] = None
    if hasattr(target, "key_fields"):
        key_field_value = target.key_fields()

    existing_children = getattr(target, "children", None)
    for name in type(target).model_fields:
        if name == "children":
            continue
        if hasattr(default_params, name):
            setattr(target, name, getattr(default_params, name))
    if existing_children is not None and hasattr(target, "children"):
        target.children = existing_children  # type: ignore[attr-defined]

    # Re-apply the original key field so the hash is stable after save/reload.
    if key_field_value is not None and hasattr(target, "apply_key"):
        target.apply_key(key_field_value)

    save_instruments_to_config(instruments, config_dir)
    logger.info("Reinitialized instrument type=%s key=%s", type_str, key)
    return {"status": "ok", "type": type_str, "key": key}


def remove_instrument(
    config_dir: str | Path,
    type_str: str,
    key: str,
) -> Dict[str, Any]:
    """Remove an instrument from config by deleting its node and re-saving the tree.

    Orphaned YAML files are cleaned up afterward.
    """
    instruments = load_instruments(config_dir)
    removed = False

    for inst_key, params in list(instruments.items()):
        if getattr(params, "type", None) == type_str and inst_key == key:
            del instruments[inst_key]
            removed = True
            break
    if not removed:
        removed = _remove_child_from_tree(instruments, type_str, key)

    if not removed:
        raise ValueError(f"Instrument type={type_str} key={key} not found in config")

    # Compute which files the REMAINING (post-removal) tree will produce BEFORE
    # writing anything, so we can diff against what's currently on disk.
    base_dir = Path(config_dir)
    inst_dir = (base_dir / "instruments").resolve()
    files_to_keep: set[Path] = set()
    for k, v in instruments.items():
        _collect_written_paths(inst_dir, inst_dir, v, str(k), files_to_keep)

    save_instruments_to_config(instruments, config_dir)

    # Delete any YAML file under instruments/ that is not referenced by the new tree.
    for f in _iter_instrument_yaml_files(base_dir):
        if f not in files_to_keep:
            try:
                f.unlink()
            except OSError:
                pass

    # Remove any directories that are now empty (deepest first so nested empties collapse).
    for d in sorted(inst_dir.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass

    logger.info("Removed instrument type=%s key=%s", type_str, key)
    return {"status": "ok", "type": type_str, "key": key}


def _find_node_in_tree(instruments: Dict[str, Any], type_str: str, key: str) -> Any:
    """Walk the instruments tree and return the Params node matching type + key."""
    for inst_key, params in instruments.items():
        if getattr(params, "type", None) == type_str and inst_key == key:
            return params
        found = _find_in_children(params, type_str, key)
        if found is not None:
            return found
    return None


def _find_in_children(parent: Any, type_str: str, key: str) -> Any:
    """Recursively search children for a node matching type + key."""
    for ck, cp in (getattr(parent, "children", {}) or {}).items():
        if getattr(cp, "type", None) == type_str and ck == key:
            return cp
        found = _find_in_children(cp, type_str, key)
        if found is not None:
            return found
    return None


def _remove_child_from_tree(instruments: Dict[str, Any], type_str: str, key: str) -> bool:
    """Walk tree and remove the first child matching type + key."""
    for params in instruments.values():
        if _remove_child_recursive(params, type_str, key):
            return True
    return False


def _remove_child_recursive(parent: Any, type_str: str, key: str) -> bool:
    """Recursively search parent's children and remove matching node."""
    children = getattr(parent, "children", {})
    if not children:
        return False
    for ck, cp in list(children.items()):
        if getattr(cp, "type", None) == type_str and ck == key:
            del children[ck]
            return True
        if _remove_child_recursive(cp, type_str, key):
            return True
    return False
