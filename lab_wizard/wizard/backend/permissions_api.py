"""Backend for the Manage Permissions page.

Surfaces the data the permission rule-builder needs and reads/writes the
``permissions:`` block of ``config/server/server.yaml``.

The vocabulary the UI offers is introspected, not hand-maintained:

- a ``when`` condition picks an instrument then a **state key**; the state keys
  for an instrument are exactly the keys its class declares via
  ``_state_methods_`` (read with :func:`collect_state_methods`),
- a ``deny`` clause picks instrument(s) then **method(s)**; the method list is
  the public methods defined in the instrument layer for that class.

Everything is derived statically from ``config/instruments`` (the same tree the
server hosts) via :class:`InstrumentRegistry` in lazy mode — no hardware is
opened to author rules.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML as RuamelYAML

from lab_wizard.lib.instruments.general.state_effects import collect_state_methods
from lab_wizard.lib.server.permissions import load_permissions
from lab_wizard.lib.server.registry import InstrumentRegistry


_INSTRUMENT_PKG = "lab_wizard.lib.instruments"


def _server_yaml_path(config_dir: str | Path) -> Path:
    return Path(config_dir) / "server" / "server.yaml"


def _public_methods(cls: type | None) -> list[str]:
    """Public method names defined for ``cls`` within the instrument layer.

    Walks the MRO and collects ordinary functions declared on classes that live
    under ``lab_wizard.lib.instruments`` — this captures behavior-ABC methods
    (``set_voltage``, ``turn_on`` …) and instrument-specific ones while skipping
    ``object`` / ABC / pydantic / typing infrastructure and properties.
    """
    if cls is None:
        return []
    names: set[str] = set()
    for klass in cls.__mro__:
        if not getattr(klass, "__module__", "").startswith(_INSTRUMENT_PKG):
            continue
        for name, member in klass.__dict__.items():
            if name.startswith("_"):
                continue
            if isinstance(member, (staticmethod, classmethod)):
                member = member.__func__
            if callable(member) and not isinstance(member, property):
                names.add(name)
    return sorted(names)


def _state_keys(cls: type | None) -> list[str]:
    """Distinct state keys the class records via ``_state_methods_``."""
    if cls is None:
        return []
    specs = collect_state_methods(cls)
    return sorted({state_key for (state_key, _value_spec) in specs.values()})


def _addressable_instruments(registry: InstrumentRegistry) -> list[dict[str, Any]]:
    """One entry per registered path that maps to an instrument class.

    Each entry carries enough for the rule builder: ``path`` (the raw handle),
    ``attribute`` (the stable handle, if named), display metadata, the
    ``state_keys`` it can be conditioned on, and the ``methods`` it can be
    denied.
    """
    path_to_attr = {p: a for a, p in registry.list_attributes().items()}
    out: list[dict[str, Any]] = []
    for path in registry.list_paths():
        cls = registry.instrument_class(path)
        if cls is None:
            continue
        desc = registry.describe_path(path)
        out.append(
            {
                "path": path,
                "attribute": path_to_attr.get(path),
                "type_hint": desc.get("type_hint"),
                "behavior_abc": desc.get("behavior_abc"),
                "state_keys": _state_keys(cls),
                "methods": _public_methods(cls),
            }
        )
    return out


def get_permissions_model(config_dir: str | Path) -> dict[str, Any]:
    """Return ``{instruments, permissions}`` for the Manage Permissions page.

    ``instruments`` is the introspected vocabulary; ``permissions`` is the
    current ``permissions:`` block from server.yaml (an empty default if none).
    """
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    permissions = _read_permissions_block(config_dir)
    return {
        "instruments": _addressable_instruments(registry),
        "permissions": permissions,
    }


def _read_permissions_block(config_dir: str | Path) -> dict[str, Any]:
    path = _server_yaml_path(config_dir)
    if not path.exists():
        return {"state_defaults": {}, "rules": []}
    yaml = RuamelYAML(typ="rt")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f) or {}
    block = data.get("permissions") or {}
    # Round-trip through plain types so the response is JSON-serializable.
    return _to_plain(block)


def _to_plain(value: Any) -> Any:
    """Recursively convert ruamel containers to plain dict/list/scalars."""
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_plain(v) for v in value]
    return value


def save_permissions(
    config_dir: str | Path, permissions: dict[str, Any]
) -> dict[str, Any]:
    """Validate and persist the ``permissions:`` block into server.yaml.

    Validation:
      * the block must parse as a :class:`PermissionsConfig` (shape/operators),
      * every ``attribute`` referenced must exist in the hosted tree, so typos
        fail loudly here rather than silently disabling a safety rule at boot.

    The rest of server.yaml (``bind``, ``config_dir``, comments) is preserved.
    Returns the persisted (plain) permissions block.
    """
    # 1. Structural validation.
    config = load_permissions(permissions)

    # 2. Referential validation: attribute names must exist.
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    known = set(registry.list_attributes())
    unknown = sorted(_referenced_attributes(config) - known)
    if unknown:
        raise ValueError(
            "Permission rules reference unknown attribute_name(s): "
            + ", ".join(unknown)
        )

    # 3. Persist, preserving the rest of server.yaml.
    path = _server_yaml_path(config_dir)
    yaml = RuamelYAML(typ="rt")
    yaml.default_flow_style = False
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f) or {}
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"server": {"bind": "tcp://0.0.0.0:12300"}}

    if permissions:
        data["permissions"] = permissions
    else:
        data.pop("permissions", None)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)

    return _to_plain(permissions)


def _referenced_attributes(config: Any) -> set[str]:
    """Collect every ``attribute`` referenced by conditions and deny clauses."""
    refs: set[str] = set()

    def _walk_condition(cond: Any) -> None:
        if cond.all_ is not None:
            for c in cond.all_:
                _walk_condition(c)
        elif cond.any_ is not None:
            for c in cond.any_:
                _walk_condition(c)
        elif cond.not_ is not None:
            _walk_condition(cond.not_)
        elif cond.attribute is not None:
            refs.add(cond.attribute)

    for rule in config.rules:
        _walk_condition(rule.when)
        for clause in rule.deny:
            if clause.attribute is not None:
                refs.add(clause.attribute)
    return refs
