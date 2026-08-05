"""Remote proxy for ``Counter`` instruments.

All abstract methods are auto-forwarded by ``RemoteProxy.__init_subclass__``
— no per-method definition is required here. Adding a new abstract method to
``Counter`` automatically extends the proxy.
"""

from __future__ import annotations

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.instruments.general.counter import Counter


class RemoteCounter(Counter, RemoteProxy):
    """A network-backed Counter. Satisfies ``isinstance(p, Counter)``."""
