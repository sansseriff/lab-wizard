"""Map DBay's reported rack state onto Lab Wizard's state-key vocabulary.

The DBay GUI backend is authoritative for its rack and broadcasts every change.
Lab Wizard's permission gate normally *infers* state from what it sent
(``_state_methods_``), which is wrong here: a physicist moving a slider in the
DBay GUI changes real hardware we never commanded, and the gate would go on
believing its own stale record. See
:mod:`lab_wizard.lib.instruments.general.transport` — this is what
``state_authority() == "subscribed"`` means.

What cannot be avoided is this translation. DBay describes a rack as modules
with addons; Lab Wizard addresses ``inst://`` paths carrying state keys like
``voltage`` and ``output``. Those are two different domain models, and no sync
library can bridge them — the mapping *is* the vendor-neutral abstraction that
lets a rule say "this VSource is biased on" without knowing DBay exists.

So it is declared, once, below. Everything here is a pure function over a
validated ``dbay.state`` snapshot: no sockets, no hardware, fully testable.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Iterator, NamedTuple, Optional


logger = logging.getLogger(__name__)

__all__ = ["ChannelState", "channel_states", "ADDON_STATE_MAP"]


class ChannelState(NamedTuple):
    """One channel's state, addressed the way DBay reports it."""

    slot: int
    addon: str  # "vsource" | "vsense"
    channel_index: int
    key: str  # Lab Wizard state key
    value: Any


def _on_off(value: Any) -> str:
    """DBay's ``activated`` flag as the gate's ``output`` vocabulary."""
    return "on" if value else "off"


def _identity(value: Any) -> Any:
    return value


# Per addon, which of its channel fields feed which Lab Wizard state key.
#
# ``activated`` is worth noting: Dac4DChannel deliberately does *not* declare an
# ``output`` state method, because driving it to 0 V is the only "off" it has —
# so inference can never populate ``output`` for a DBay channel. Subscription
# can, because the GUI server tracks it. The subscribed path is therefore
# strictly better informed than the inferred one, not merely more current.
ADDON_STATE_MAP: dict[str, dict[str, tuple[str, Callable[[Any], Any]]]] = {
    "vsource": {
        "bias_voltage": ("voltage", _identity),
        "activated": ("output", _on_off),
    },
    "vsense": {
        "voltage": ("voltage", _identity),
    },
}

# Module-level fields that are a single channel rather than a channel list.
# dac16D's shared bias (``vsb``) and reference read (``vr``) are addons with one
# channel, so they are addressed as channel 0 of a synthetic addon name.
SINGLETON_ADDONS: dict[str, str] = {
    "vsb": "vsource",
    "vr": "vsense",
}


def _channel_fields(channel: Any) -> dict[str, Any]:
    if isinstance(channel, dict):
        return channel
    return {
        name: getattr(channel, name)
        for name in getattr(type(channel), "model_fields", {})
    }


def _emit(
    slot: int, addon: str, index: int, fields: dict[str, Any]
) -> Iterator[ChannelState]:
    mapping = ADDON_STATE_MAP.get(addon)
    if not mapping:
        return
    for field, (key, convert) in mapping.items():
        if field not in fields:
            continue
        yield ChannelState(slot, addon, index, key, convert(fields[field]))


def _module_slot(module: Any) -> Optional[int]:
    core = module.get("core") if isinstance(module, dict) else getattr(module, "core", None)
    if core is None:
        return None
    slot = core.get("slot") if isinstance(core, dict) else getattr(core, "slot", None)
    return slot if isinstance(slot, int) else None


def _get(obj: Any, name: str) -> Any:
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def channel_states(snapshot: Any) -> Iterator[ChannelState]:
    """Yield every channel state a DBay snapshot reports.

    Accepts a validated ``dbay.state.SystemState`` or the raw snapshot mapping,
    so this works whether or not the caller has the schema available. Unknown
    modules (``UnknownModuleState``) simply contribute nothing rather than
    breaking the sweep — a rack may hold modules this build predates.
    """
    modules = _get(snapshot, "data") or []
    if not isinstance(modules, list):
        return

    for module in modules:
        slot = _module_slot(module)
        if slot is None:
            continue

        for addon_name in ("vsource", "vsense"):
            addon = _get(module, addon_name)
            if addon is None:
                continue
            channels = _get(addon, "channels") or []
            if not isinstance(channels, list):
                continue
            for position, channel in enumerate(channels):
                fields = _channel_fields(channel)
                # Prefer the channel's own index; positional order is a
                # fallback for payloads that omit it.
                index = fields.get("index")
                index = index if isinstance(index, int) else position
                yield from _emit(slot, addon_name, index, fields)

        for field_name, addon_kind in SINGLETON_ADDONS.items():
            single = _get(module, field_name)
            if single is None:
                continue
            fields = _channel_fields(single)
            index = fields.get("index")
            yield from _emit(
                slot, addon_kind, index if isinstance(index, int) else 0, fields
            )
