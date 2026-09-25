"""Discovery against an unsaved parameter tree, with root-level cleanup."""

from __future__ import annotations

from lab_wizard.lib.server.registry import InstrumentRegistry, PATH_PREFIX
from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    prepare_instrument_chain,
)
from lab_wizard.lib.utilities.resource_catalog import load_params_class


def draft_discovery_tree(config_dir, chain):
    instruments = load_instruments(config_dir, include_disabled=True)
    path = prepare_instrument_chain(instruments, chain)["path"]
    root = path[0]["key"]

    def active_children(node):
        children = getattr(node, "children", None)
        if isinstance(children, dict):
            node.children = {
                key: active_children(child)
                for key, child in children.items()
                if getattr(child, "enabled", True)
            }
        return node

    # Disabled addresses still participate in validation, but discovery must
    # expose the same active hardware tree as ordinary runtime loading.
    return (
        InstrumentRegistry.from_instruments({root: active_children(instruments[root])}),
        path,
    )


def discover_with_registry(registry, path, type, action, params):
    actions = {a.name: a for a in load_params_class(type).discovery_actions()}
    if action not in actions:
        raise ValueError(f"Type {type!r} has no discovery action {action!r}")
    root = PATH_PREFIX + path[0]["key"] if path else None
    try:
        parent = (
            registry.resolve(PATH_PREFIX + "/".join(p["key"] for p in path))
            if path
            else None
        )
        return actions[action].run(params, parent=parent).model_dump()
    finally:
        if root:
            registry.release(root)
