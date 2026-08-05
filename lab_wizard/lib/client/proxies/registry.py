"""Map a server-reported ``behavior_abc`` to the client proxy class for it.

This is the seam where a new behavior interface gets its network face. Adding
one means writing a one-line proxy (see ``vsource.py``) and adding it here,
keyed on **the ABC class itself** — never on its name. The wire carries a
string, but that string is derived from ``cls.__name__`` on both sides, so the
two ends cannot drift apart the way two hand-typed tables did.

A behavior with no proxy resolves to ``RemoteOpaque``, which still works by
reflective dispatch but loses the type identity — ``isinstance(p, Counter)``
fails and an editor cannot autocomplete it. That is a deliberate fallback for
instruments with no declared behavior, not a substitute for a proxy, so
``tests/test_behaviors.py`` asserts every registered behavior is either covered
here or listed as a considered exemption.
"""

from __future__ import annotations

from typing import Type

from lab_wizard.lib.client.proxies.base import RemoteOpaque, RemoteProxy
from lab_wizard.lib.client.proxies.counter import RemoteCounter
from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource


PROXY_BY_BEHAVIOR: dict[type, Type[RemoteProxy]] = {
    VSource: RemoteVSource,
    VSense: RemoteVSense,
    Counter: RemoteCounter,
}

# Behaviors that intentionally have no proxy, with the reason. ChannelProvider
# hands out *other instruments*; a remote client reaches those by their own
# attribute names, so proxying the container would add a hop to nothing.
PROXY_EXEMPT: dict[str, str] = {
    "ChannelProvider": (
        "channels are addressed as instruments in their own right, so there is "
        "nothing for a container proxy to return"
    ),
}

# Name-keyed view, derived — this is what the wire protocol actually looks up.
PROXY_BY_BEHAVIOR_ABC: dict[str, Type[RemoteProxy]] = {
    abc.__name__: proxy for abc, proxy in PROXY_BY_BEHAVIOR.items()
}


def proxy_class_for(behavior_abc: str | None) -> Type[RemoteProxy]:
    """Return the proxy class for an ABC name, or ``RemoteOpaque`` as fallback."""
    if behavior_abc is None:
        return RemoteOpaque
    return PROXY_BY_BEHAVIOR_ABC.get(behavior_abc, RemoteOpaque)
