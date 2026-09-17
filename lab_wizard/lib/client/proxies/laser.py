"""Remote proxy for ``Laser`` instruments.

All abstract methods are auto-forwarded by ``RemoteProxy.__init_subclass__``
— no per-method definition is required here. ``enter_safe_state`` is concrete
on the ABC and deliberately *not* overridden: it runs client-side as a
forwarded ``turn_off``, which the server's permission gate records.
"""

from __future__ import annotations

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.instruments.general.laser import Laser


class RemoteLaser(Laser, RemoteProxy):
    """A network-backed Laser. Satisfies ``isinstance(p, Laser)``."""
