"""Remote proxy for ``Attenuator`` instruments.

All abstract methods are auto-forwarded by ``RemoteProxy.__init_subclass__``
— no per-method definition is required here. ``enter_safe_state`` is concrete
on the ABC and deliberately *not* overridden: it runs client-side as
``close_shutter`` then ``set_attenuation``, two forwarded calls the server's
permission gate records individually.
"""

from __future__ import annotations

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.instruments.general.attenuator import Attenuator


class RemoteAttenuator(Attenuator, RemoteProxy):
    """A network-backed Attenuator. Satisfies ``isinstance(p, Attenuator)``."""
