"""Remote proxy for ``VSense`` instruments.

All abstract methods are auto-forwarded by ``RemoteProxy.__init_subclass__``.
``VSense.measure()`` is concrete (it calls ``self.get_voltage()``); on a proxy
``get_voltage`` is itself a forwarder, so ``measure()`` works correctly with
a single RPC.
"""

from __future__ import annotations

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.instruments.general.vsense import VSense


class RemoteVSense(VSense, RemoteProxy):
    """A network-backed VSense. Satisfies ``isinstance(p, VSense)``."""
