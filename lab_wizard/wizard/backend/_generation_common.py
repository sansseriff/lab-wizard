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

from lab_wizard.lib.utilities.params_discovery import get_parent_chain, get_type_to_module_map


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


def _clone_without_children(params: Any) -> Any:
    clone = params.model_copy(deep=True)
    if hasattr(clone, "children"):
        clone.children = {}  # type: ignore[attr-defined]
    return clone


def _build_subset_instruments_from_leaves(leaves: list[_NodeRef]) -> dict[str, Any]:
    roots: dict[str, Any] = {}

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
        if not chain:
            continue

        root_node = chain[0]
        if root_node.key not in roots:
            roots[root_node.key] = _clone_without_children(root_node.params)
        parent_clone = roots[root_node.key]

        for node in chain[1:]:
            children = getattr(parent_clone, "children", None)
            if children is None:
                raise ValueError(
                    f"Node {type(parent_clone).__name__} unexpectedly has no children field"
                )
            existing = children.get(node.key)
            if existing is None:
                child_clone = _clone_without_children(node.params)
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


def _type_info(type_str: str) -> tuple[str, str]:
    m = get_type_to_module_map().get(type_str)
    if m is None:
        raise ValueError(f"Unknown instrument type '{type_str}'")
    params_class = str(m["class_name"])
    inst_class = params_class[:-6] if params_class.endswith("Params") else params_class
    return str(m["module"]), params_class if params_class else inst_class


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
