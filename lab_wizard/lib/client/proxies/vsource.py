"""Remote proxy for ``VSource`` instruments.

All abstract methods are auto-forwarded by ``RemoteProxy.__init_subclass__``
— no per-method definition is required here. Adding a new abstract method to
``VSource`` automatically extends the proxy.
"""

from __future__ import annotations

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.instruments.general.vsource import VSource


class RemoteVSource(VSource, RemoteProxy):
    """A network-backed VSource. Satisfies ``isinstance(p, VSource)``."""
