"""Map server-reported ``behavior_abc`` strings to client proxy classes.

This is the seam where a new behavior interface (e.g. a future ``FunctionGen``)
gets wired in: add the proxy class file under ``proxies/`` and add one entry
here. The server's ``describe_attribute`` will return the matching ABC name
and the client will pick the right proxy automatically.

The strings on this side must match the names returned by
:data:`lab_wizard.lib.server.registry._BEHAVIOR_ABCS`.
"""

from __future__ import annotations

from typing import Type

from lab_wizard.lib.client.proxies.base import RemoteOpaque, RemoteProxy
from lab_wizard.lib.client.proxies.vsense import RemoteVSense
from lab_wizard.lib.client.proxies.vsource import RemoteVSource


PROXY_BY_BEHAVIOR_ABC: dict[str, Type[RemoteProxy]] = {
    "VSource": RemoteVSource,
    "VSense": RemoteVSense,
    # "ChannelProvider": RemoteChannelProvider,  # Phase 4 / future
}


def proxy_class_for(behavior_abc: str | None) -> Type[RemoteProxy]:
    """Return the proxy class for an ABC name, or ``RemoteOpaque`` as fallback."""
    if behavior_abc is None:
        return RemoteOpaque
    return PROXY_BY_BEHAVIOR_ABC.get(behavior_abc, RemoteOpaque)
