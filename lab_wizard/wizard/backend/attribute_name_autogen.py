"""Auto-generate ``attribute_name`` values for custom-resource selections.

When the wizard's *Create Custom Resource* feature generates code in
``from_attribute`` style, every selected leaf (or channel) must have a
non-empty ``attribute_name`` so that ``Exp.from_attribute(...)`` can find
it at runtime. This module fills in any missing names by deriving a
unique identifier from the instrument type, checked for collisions
against the *entire* configured instruments tree (``Exp.from_attribute``
walks the whole tree and the first match wins, so global uniqueness is
required).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from lab_wizard.wizard.backend._generation_common import (
    _NodeRef,
    _sanitize_identifier,
)


@dataclass
class AutogenMutation:
    leaf: _NodeRef
    channel_index: int | None
    chosen_name: str


def _collect_existing_attribute_names(instruments: dict[str, Any]) -> set[str]:
    """Walk the full instruments tree and return every non-empty attribute_name.

    Mirrors the search semantics of
    :func:`lab_wizard.lib.utilities.model_tree._find_attribute_path`: node-level
    ``attribute_name`` *and* each ``channels[i].attribute_name``, recursing
    into ``children`` dicts.
    """

    out: set[str] = set()

    def _recurse(params: Any) -> None:
        name = getattr(params, "attribute_name", None)
        if isinstance(name, str) and name:
            out.add(name)

        channels = getattr(params, "channels", None)
        if isinstance(channels, list):
            for ch in channels:
                ch_name = getattr(ch, "attribute_name", None)
                if isinstance(ch_name, str) and ch_name:
                    out.add(ch_name)

        children = getattr(params, "children", None)
        if isinstance(children, dict):
            for child in children.values():
                _recurse(child)

    for root in instruments.values():
        _recurse(root)

    return out


def _current_name(leaf: _NodeRef, channel_index: int | None) -> str:
    if channel_index is not None:
        ch_list = getattr(leaf.params, "channels", None)
        if isinstance(ch_list, list) and 0 <= channel_index < len(ch_list):
            return getattr(ch_list[channel_index], "attribute_name", "") or ""
        return ""
    return getattr(leaf.params, "attribute_name", "") or ""


def _assign_name(leaf: _NodeRef, channel_index: int | None, name: str) -> None:
    if channel_index is not None:
        ch_list = getattr(leaf.params, "channels", None)
        ch_list[channel_index].attribute_name = name  # type: ignore[index]
    else:
        leaf.params.attribute_name = name


def _base_name(leaf: _NodeRef, channel_index: int | None) -> str:
    type_token = _sanitize_identifier(leaf.type)
    if channel_index is None:
        return type_token
    return f"{type_token}_ch{channel_index}"


def _unique_name(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def autogen_attribute_names(
    instruments: dict[str, Any],
    targets: list[tuple[_NodeRef, int | None]],
) -> list[AutogenMutation]:
    """Fill in empty ``attribute_name`` fields for the given targets.

    ``targets`` is a list of ``(leaf, channel_index)`` tuples identifying
    each selection that needs a name. For targets that already have a
    non-empty name, nothing happens. For empty ones, a unique name is
    chosen from ``{type}`` (or ``{type}_ch{n}``) with ``_2``, ``_3``…
    suffixing on collision against both the pre-existing tree and names
    allocated earlier in this same call.

    Returns the list of mutations actually applied (empty if every target
    already had a name).
    """

    taken = _collect_existing_attribute_names(instruments)
    mutations: list[AutogenMutation] = []

    for leaf, channel_index in targets:
        if _current_name(leaf, channel_index):
            continue
        base = _base_name(leaf, channel_index)
        chosen = _unique_name(base, taken)
        _assign_name(leaf, channel_index, chosen)
        taken.add(chosen)
        mutations.append(
            AutogenMutation(
                leaf=leaf, channel_index=channel_index, chosen_name=chosen
            )
        )

    return mutations
