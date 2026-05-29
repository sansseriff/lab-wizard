"""Shared helpers for project / custom-resource generation.

These were originally inlined in :mod:`project_generation`; they were lifted
here so the create-custom-resource workflow can reuse the same tree walking,
selection resolution, parent-chain validation, and identifier/type helpers
without duplicating code.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from pydantic import BaseModel

from lab_wizard.lib.utilities.params_discovery import (
    Kind,
    get_parent_chain,
    get_type_to_module_map,
)


class SelectedNodeRef(BaseModel):
    type: str
    key: str


class BaseSelection(BaseModel):
    """Common shape for selecting a node in the configured instrument tree.

    Concrete subclasses (measurement requirements, custom resources) add their
    own fields (e.g. ``channel_index``) on top of these.
    """

    variable_name: str
    type: str
    key: str
    # Optional explicit chain. Accepts either leaf->root or root->leaf.
    path: list[SelectedNodeRef] | None = None


@dataclass
class _NodeRef:
    key: str
    params: Any
    parent: "_NodeRef | None"

    @property
    def type(self) -> str:
        return str(getattr(self.params, "type", ""))


def _walk_tree(instruments: dict[str, Any]) -> list[_NodeRef]:
    out: list[_NodeRef] = []

    def _recurse(key: str, params: Any, parent: _NodeRef | None) -> None:
        node = _NodeRef(key=key, params=params, parent=parent)
        out.append(node)
        for child_key, child_params in (getattr(params, "children", {}) or {}).items():
            _recurse(str(child_key), child_params, node)

    for top_key, top_params in instruments.items():
        _recurse(str(top_key), top_params, None)
    return out


def _normalize_path(sel: BaseSelection) -> list[SelectedNodeRef] | None:
    if not sel.path:
        return None
    # Canonicalize to leaf->root shape.
    if sel.path[0].type == sel.type and sel.path[0].key == sel.key:
        return sel.path
    if sel.path[-1].type == sel.type and sel.path[-1].key == sel.key:
        return list(reversed(sel.path))
    raise ValueError(
        f"path for {sel.variable_name} must include leaf ({sel.type}, {sel.key})"
    )


def _node_lineage_leaf_to_root(node: _NodeRef) -> list[_NodeRef]:
    out: list[_NodeRef] = []
    cur: _NodeRef | None = node
    while cur is not None:
        out.append(cur)
        cur = cur.parent
    return out


def _validate_parent_chain(leaf: _NodeRef) -> None:
    actual = _node_lineage_leaf_to_root(leaf)
    actual_types = [n.type for n in actual]
    expected_types = [leaf.type] + get_parent_chain(leaf.type)
    if actual_types != expected_types:
        raise ValueError(
            "Tree lineage does not match parent_class chain for leaf "
            f"{leaf.type}:{leaf.key}. Actual={actual_types}, expected={expected_types}"
        )


def _resolve_selection_node(
    sel: BaseSelection,
    all_nodes: list[_NodeRef],
) -> _NodeRef:
    normalized = _normalize_path(sel)
    candidates = [n for n in all_nodes if n.type == sel.type and n.key == sel.key]
    if not candidates:
        raise ValueError(
            f"Selected leaf not found in configured tree: {sel.type}:{sel.key}"
        )
    if normalized is None:
        if len(candidates) > 1:
            raise ValueError(
                f"Selection {sel.type}:{sel.key} is ambiguous; provide explicit path"
            )
        _validate_parent_chain(candidates[0])
        return candidates[0]

    want = [(p.type, p.key) for p in normalized]
    for node in candidates:
        lineage = _node_lineage_leaf_to_root(node)
        have = [(n.type, n.key) for n in lineage]
        if have == want:
            _validate_parent_chain(node)
            return node

    raise ValueError(
        f"Could not match selection path for {sel.variable_name}. Wanted {want}."
    )


def _lineage_key(node: _NodeRef) -> tuple[tuple[str, str], ...]:
    return tuple((n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(node)))


def _selected_channel_indices_by_lineage(
    *,
    selections: list[Any],
    leaves: list[_NodeRef],
) -> dict[tuple[tuple[str, str], ...], set[int] | None]:
    selected: dict[tuple[tuple[str, str], ...], set[int] | None] = {}
    for sel, leaf in zip(selections, leaves):
        key = _lineage_key(leaf)
        channel_index = getattr(sel, "channel_index", None)
        if channel_index is None:
            selected[key] = None
            continue
        if selected.get(key) is None and key in selected:
            continue
        selected.setdefault(key, set())
        indices = selected[key]
        if indices is not None:
            indices.add(channel_index)
    return selected


def _trim_channels_for_selection(params: Any, channel_indices: set[int] | None) -> Any:
    if channel_indices is None or not hasattr(params, "channels"):
        return params
    channels = getattr(params, "channels")
    if not isinstance(channels, list) or not channel_indices:
        return params
    max_index = max(channel_indices)
    if max_index >= len(channels):
        raise ValueError(
            f"Selected channel index {max_index} out of range for "
            f"{type(params).__name__}.channels"
        )
    params.channels = channels[: max_index + 1]  # type: ignore[attr-defined]
    return params


def _clone_without_children(
    params: Any,
    channel_indices: set[int] | None = None,
) -> Any:
    clone = params.model_copy(deep=True)
    if hasattr(clone, "children"):
        clone.children = {}  # type: ignore[attr-defined]
    return _trim_channels_for_selection(clone, channel_indices)


def _build_subset_instruments_from_leaves(leaves: list[_NodeRef]) -> dict[str, Any]:
    return _build_subset_instruments_from_selected_nodes(
        [(leaf, None) for leaf in leaves]
    )


def _build_subset_instruments_from_selected_nodes(
    selected_nodes: list[tuple[_NodeRef, int | None]],
) -> dict[str, Any]:
    roots: dict[str, Any] = {}
    selected_channels: dict[tuple[tuple[str, str], ...], set[int] | None] = {}
    for leaf, channel_index in selected_nodes:
        key = _lineage_key(leaf)
        if channel_index is None:
            selected_channels[key] = None
            continue
        if selected_channels.get(key) is None and key in selected_channels:
            continue
        selected_channels.setdefault(key, set())
        indices = selected_channels[key]
        if indices is not None:
            indices.add(channel_index)

    for leaf, _channel_index in selected_nodes:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
        if not chain:
            continue

        root_node = chain[0]
        if root_node.key not in roots:
            roots[root_node.key] = _clone_without_children(
                root_node.params,
                selected_channels.get(_lineage_key(root_node)),
            )
        parent_clone = roots[root_node.key]
        lineage_id = [(root_node.type, root_node.key)]

        for node in chain[1:]:
            lineage_id.append((node.type, node.key))
            children = getattr(parent_clone, "children", None)
            if children is None:
                raise ValueError(
                    f"Node {type(parent_clone).__name__} unexpectedly has no children field"
                )
            existing = children.get(node.key)
            if existing is None:
                child_clone = _clone_without_children(
                    node.params,
                    selected_channels.get(tuple(lineage_id)),
                )
                children[node.key] = child_clone
                existing = child_clone
            parent_clone = existing

    return roots


def _sanitize_identifier(raw: str) -> str:
    out = re.sub(r"[^0-9a-zA-Z_]", "_", raw)
    out = re.sub(r"_+", "_", out).strip("_")
    if not out:
        out = "node"
    if out[0].isdigit():
        out = f"n_{out}"
    return out


def _short_type_token(type_str: str) -> str:
    """Short token used for generated variable names."""
    token = _sanitize_identifier(type_str).lower().split("_")[0]
    return token or "node"


def _type_info(type_str: str, kind: Kind = "instrument") -> tuple[str, str]:
    m = get_type_to_module_map(kind).get(type_str)
    if m is None:
        raise ValueError(f"Unknown {kind} type '{type_str}'")
    params_class = str(m["class_name"])
    inst_class = params_class[:-6] if params_class.endswith("Params") else params_class
    return str(m["module"]), params_class if params_class else inst_class


def _params_kwargs_literal(
    params: Any,
    channel_indices: set[int] | None = None,
) -> str:
    """Return a Python-literal kwargs dict for a Params object, excluding children."""
    if not hasattr(params, "model_dump"):
        return "{}"
    params_for_dump = _trim_channels_for_selection(
        params.model_copy(deep=True),
        channel_indices,
    )
    data = params_for_dump.model_dump(exclude={"children"}, exclude_none=True)
    return repr(data)


def _runtime_class_name(params_class_name: str) -> str:
    return (
        params_class_name[:-6]
        if params_class_name.endswith("Params")
        else params_class_name
    )


def _selected_runtime_type(leaf: _NodeRef, channel_index: int | None) -> str:
    _module, params_cls = _type_info(leaf.type)
    inst_cls = _runtime_class_name(params_cls)
    if channel_index is not None:
        return f"{inst_cls}Channel"
    return inst_cls


def _selected_runtime_imports(
    *,
    selections: list[Any],
    leaves: list[_NodeRef],
) -> set[tuple[str, str]]:
    imports: set[tuple[str, str]] = set()
    for sel, leaf in zip(selections, leaves):
        channel_index = getattr(sel, "channel_index", None)
        module, params_cls = _type_info(leaf.type)
        imports.add((module, _selected_runtime_type(leaf, channel_index)))
        if channel_index is not None:
            imports.add((module, _runtime_class_name(params_cls)))
    return imports


def _compose_pedagogical_yaml_expanded(
    *,
    selections: list[Any],
    var_names: list[str],
    leaves: list[_NodeRef],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Generate explicit hierarchy construction from parsed project YAML.

    This intentionally avoids ``from_config``. It shows the hash-key traversal
    through ``project.resources.instruments`` / ``params.children`` and then
    uses explicit runtime construction:
    root ``params.create_inst()`` and child ``ChildClass.from_parent(parent, params)``.
    """
    created_inst: dict[tuple[tuple[str, str], ...], str] = {}
    created_params: dict[tuple[tuple[str, str], ...], str] = {}
    lines: list[str] = ["resource_config = project.resources"]
    import_pairs: set[tuple[str, str]] = set()
    used_inst_names: dict[str, int] = {}

    def _alloc(base: str) -> str:
        count = used_inst_names.get(base, 0) + 1
        used_inst_names[base] = count
        return base if count == 1 else f"{base}_{count}"

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))
        lineage_id: list[tuple[str, str]] = []
        parent_params_var: str | None = None
        for idx, node in enumerate(chain):
            lineage_id.append((node.type, node.key))
            key_t = tuple(lineage_id)
            if key_t in created_inst:
                parent_params_var = created_params[key_t]
                continue

            module, params_cls = _type_info(node.type)
            inst_cls = _runtime_class_name(params_cls)
            import_pairs.add((module, params_cls))
            import_pairs.add((module, inst_cls))

            token = _short_type_token(node.type)
            key_const = (
                f"{_sanitize_identifier(token).upper()}_{len(created_inst) + 1}_KEY"
            )
            params_var = _alloc(f"{token}_params")
            inst_var = _alloc(f"{token}_i")
            created_params[key_t] = params_var
            created_inst[key_t] = inst_var

            lines.append(f"{key_const} = {node.key!r}")
            if idx == 0:
                lines.append(
                    f"{params_var}: {params_cls} = "
                    f"{params_cls}.model_validate(resource_config.instruments[{key_const}])"
                )
                lines.append(f"{inst_var}: {inst_cls} = {params_var}.create_inst()")
            else:
                assert parent_params_var is not None
                parent_inst = created_inst[tuple(lineage_id[:-1])]
                lines.append(
                    f"{params_var}: {params_cls} = "
                    f"{params_cls}.model_validate({parent_params_var}.children[{key_const}])"
                )
                lines.append(
                    f"{inst_var}: {inst_cls} = "
                    f"{inst_cls}.from_parent({parent_inst}, {params_var}, key={key_const})"
                )
            lines.append("")
            parent_params_var = params_var

    final_exprs: list[str] = []
    for sel, _var_name, leaf in zip(selections, var_names, leaves):
        chain_key = tuple(
            (n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(leaf))
        )
        base_inst = created_inst[chain_key]
        channel_index = getattr(sel, "channel_index", None)
        if channel_index is not None:
            final_exprs.append(f"{base_inst}.channels[{channel_index}]")
        else:
            final_exprs.append(base_inst)

    import_pairs.update(_selected_runtime_imports(selections=selections, leaves=leaves))
    return lines, sorted(import_pairs), final_exprs


def _compose_pedagogical_embedded(
    *,
    selections: list[Any],
    var_names: list[str],
    leaves: list[_NodeRef],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Generate explicit hierarchy construction with Params embedded in Python.

    The emitted file is intentionally brittle: changing central YAML later will
    not update this script. Its purpose is to teach how Params objects, hash-keyed
    child dictionaries, and live instrument objects fit together.
    """
    created_inst: dict[tuple[tuple[str, str], ...], str] = {}
    created_params: dict[tuple[tuple[str, str], ...], str] = {}
    lines: list[str] = []
    import_pairs: set[tuple[str, str]] = set()
    used_inst_names: dict[str, int] = {}
    selected_channels = _selected_channel_indices_by_lineage(
        selections=selections,
        leaves=leaves,
    )

    def _alloc(base: str) -> str:
        count = used_inst_names.get(base, 0) + 1
        used_inst_names[base] = count
        return base if count == 1 else f"{base}_{count}"

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))
        lineage_id: list[tuple[str, str]] = []
        for node in chain:
            lineage_id.append((node.type, node.key))
            key_t = tuple(lineage_id)
            if key_t in created_params:
                continue

            module, params_cls = _type_info(node.type)
            inst_cls = _runtime_class_name(params_cls)
            import_pairs.add((module, params_cls))
            import_pairs.add((module, inst_cls))
            token = _short_type_token(node.type)
            key_const = (
                f"{_sanitize_identifier(token).upper()}_{len(created_params) + 1}_KEY"
            )
            params_var = _alloc(f"{token}_params")
            created_params[key_t] = params_var

            lines.append(f"{key_const} = {node.key!r}")
            lines.append(
                f"{params_var}: {params_cls} = "
                f"{params_cls}.model_validate("
                f"{_params_kwargs_literal(node.params, selected_channels.get(key_t))})"
            )
            lines.append("")

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))
        lineage_id = []
        for idx, node in enumerate(chain):
            lineage_id.append((node.type, node.key))
            key_t = tuple(lineage_id)
            if key_t in created_inst:
                continue
            token = _short_type_token(node.type)
            inst_var = _alloc(f"{token}_i")
            created_inst[key_t] = inst_var
            params_var = created_params[key_t]
            _module, params_cls = _type_info(node.type)
            inst_cls = _runtime_class_name(params_cls)
            if idx == 0:
                lines.append(f"{inst_var}: {inst_cls} = {params_var}.create_inst()")
            else:
                parent_inst = created_inst[tuple(lineage_id[:-1])]
                key_const = f"{_sanitize_identifier(token).upper()}_{list(created_params).index(key_t) + 1}_KEY"
                lines.append(
                    f"{inst_var}: {inst_cls} = "
                    f"{inst_cls}.from_parent({parent_inst}, {params_var}, key={key_const})"
                )

    final_exprs: list[str] = []
    for sel, _var_name, leaf in zip(selections, var_names, leaves):
        chain_key = tuple(
            (n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(leaf))
        )
        base_inst = created_inst[chain_key]
        channel_index = getattr(sel, "channel_index", None)
        if channel_index is not None:
            final_exprs.append(f"{base_inst}.channels[{channel_index}]")
        else:
            final_exprs.append(base_inst)

    import_pairs.update(_selected_runtime_imports(selections=selections, leaves=leaves))
    return lines, sorted(import_pairs), final_exprs


def _create_unique_project_dir(projects_dir: Path, prefix: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{prefix}_{ts}"
    candidate = projects_dir / base_name
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate
    i = 1
    while True:
        attempt = projects_dir / f"{base_name}_{i}"
        if not attempt.exists():
            attempt.mkdir(parents=True, exist_ok=False)
            return attempt
        i += 1
