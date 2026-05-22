"""Auto-generate ``attribute_name`` values for custom-resource selections.

When the wizard's *Create Custom Resource* feature generates code in
``from_attribute`` style, every selected leaf (or channel) must have a
non-empty ``attribute_name`` so that ``Exp.from_attribute(...)`` can find
it at runtime. This module fills in any missing names by deriving a
unique identifier from the instrument type, checked for collisions
against the *entire* configured instruments tree (``Exp.from_attribute``
walks the whole tree and the first match wins, so global uniqueness is
required).

Default names use a hybrid ``{type}-{petname}`` form (e.g.
``dac4d-vanilla-seafoam``):

- the **type** prefix tells you what the instrument is at a glance, and
- the **petname** suffix (a random adjective-noun slug from ``coolname``) is
  globally unique and *stable*: it is generated once and stored in the YAML,
  never derived from key fields, so it survives slot/port edits — unlike the
  ``inst://`` hash and unlike a positional ``_ch{n}`` / ``_2`` suffix.

This is a strong nudge to rename to something semantic; the petname is just a
safe, memorable default.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import coolname

from lab_wizard.wizard.backend._generation_common import (
    _NodeRef,
    _sanitize_identifier,
)


def _default_petname() -> str:
    """Return a two-word slug like ``vanilla-seafoam``."""
    return coolname.generate_slug(2)


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


def _type_token(leaf: _NodeRef) -> str:
    """Lowercased, identifier-safe instrument type, e.g. ``dac4d``."""
    return _sanitize_identifier(leaf.type).lower()


# How many fresh petnames to try before falling back to numeric suffixing.
# coolname's space is enormous, so a collision essentially never happens; this
# bound just guarantees termination if a caller injects a degenerate generator.
_MAX_PETNAME_TRIES = 50


def _unique_petname_name(
    type_token: str,
    taken: set[str],
    petname_fn: Callable[[], str],
) -> str:
    """``{type}-{petname}`` not present in ``taken``.

    Tries fresh petnames; if all collide (only possible with a degenerate
    generator) falls back to ``{type}-{petname}-2``, ``-3``, … on the last one.
    """
    candidate = f"{type_token}-{petname_fn()}"
    for _ in range(_MAX_PETNAME_TRIES):
        if candidate not in taken:
            return candidate
        candidate = f"{type_token}-{petname_fn()}"
    base = candidate
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def autogen_attribute_names(
    instruments: dict[str, Any],
    targets: list[tuple[_NodeRef, int | None]],
    *,
    petname_fn: Callable[[], str] = _default_petname,
) -> list[AutogenMutation]:
    """Fill in empty ``attribute_name`` fields for the given targets.

    ``targets`` is a list of ``(leaf, channel_index)`` tuples identifying
    each selection that needs a name. For targets that already have a
    non-empty name, nothing happens. For empty ones, a unique
    ``{type}-{petname}`` name is chosen (see module docstring), checked for
    collisions against both the pre-existing tree and names allocated earlier
    in this same call.

    ``petname_fn`` is injectable for deterministic testing; it defaults to a
    two-word ``coolname`` slug.

    Returns the list of mutations actually applied (empty if every target
    already had a name).
    """

    taken = _collect_existing_attribute_names(instruments)
    mutations: list[AutogenMutation] = []

    for leaf, channel_index in targets:
        if _current_name(leaf, channel_index):
            continue
        chosen = _unique_petname_name(_type_token(leaf), taken, petname_fn)
        _assign_name(leaf, channel_index, chosen)
        taken.add(chosen)
        mutations.append(
            AutogenMutation(
                leaf=leaf, channel_index=channel_index, chosen_name=chosen
            )
        )

    return mutations
