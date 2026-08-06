"""FAKE53220A — a Keysight counter with no Keysight behind it.

The driver *is* :class:`Keysight53220A`: same ``CONFigure`` sequencing, same
input conditioning, same ``READ?`` parsing, same ``Counter`` behaviour on each
channel. Only the params type is new, so the wizard can offer it as its own
instrument and a config tree can tell a simulated counter from a real one, and
only the VISA session is replaced — by
:class:`~lab_wizard.lib.instruments.fake_rack.virtual_counter.FakeVisaDep`,
which hands the SCPI to a simulated counter watching a simulated detector.

**Wiring.** A counter is its own root in the config tree, so it cannot inherit
a detector from a mainframe the way a voltage source does. It names one
instead: give this instrument and a ``fake900`` the same ``detector_name`` and
they are wired to the same device, which is what makes a simulated PCR curve
mean anything — the bias the source applies decides the counts this reports.
See :mod:`lab_wizard.lib.instruments.fake_rack.wiring`.
"""

from __future__ import annotations

import logging
from typing import ClassVar, Literal

from pydantic import Field

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModelParams
from lab_wizard.lib.instruments.fake_rack.virtual_counter import (
    FakeVisaDep,
    VirtualKeysightCounter,
)
from lab_wizard.lib.instruments.fake_rack.wiring import shared_detector
from lab_wizard.lib.instruments.keysight53220A import (
    Keysight53220A,
    Keysight53220AParams,
)

logger = logging.getLogger("lab_wizard.lib.instruments.fake_rack.fake_counter")

# Not an IP address, and not meant to look like one: a config tree should say
# plainly which instruments are real.
DEFAULT_FAKE_COUNTER_ADDRESS = "sim://fake-counter-0"


class FakeCounterParams(Keysight53220AParams):
    """Params for the simulated counter.

    Inherits every field of the real counter — trigger, gate, timeouts, and the
    per-channel input conditioning — so a simulated project and a real one have
    the same shape and exercise the same generated code paths. What it adds is
    the detector: which one, and (when it creates it) what it is made of.
    """

    type: Literal["fake_counter"] = "fake_counter"  # type: ignore[assignment]
    ip_address: str = DEFAULT_FAKE_COUNTER_ADDRESS
    key_hint: ClassVar[str] = "Simulated address (e.g. sim://fake-counter-0)"
    device: SnspdModelParams = Field(
        default_factory=SnspdModelParams,
        description="Constants for the detector, if this instrument is the one that creates it",
    )
    detector_name: str = Field(
        default="",
        description=(
            "Name of the shared detector this counter watches; must match the "
            "fake900 driving it. Empty gives a private, unbiased detector"
        ),
    )

    @classmethod
    def resource_class(cls) -> type["FakeCounter"]:
        return FakeCounter

    def create_inst(self) -> "FakeCounter":
        return FakeCounter.from_params(self)

    # -- Transport ----------------------------------------------------------
    # Exclusive like the real counter, even though nothing physical is at
    # stake: the transport-arbitration machinery is one of the layers these
    # instruments exist to let other tests exercise honestly.

    def transport_key(self) -> str | None:
        return self.ip_address or None

    # -- Simulation ---------------------------------------------------------

    def virtual_counter(self) -> VirtualKeysightCounter:
        """Build the emulated counter this params entry describes."""
        return VirtualKeysightCounter(shared_detector(self.detector_name, self.device))


class FakeCounter(Keysight53220A):
    """Keysight 53220A driver pointed at a simulated counter.

    Only :meth:`from_params` differs from :class:`Keysight53220A`: it
    substitutes the VISA session. Channel construction, measurement arming, and
    every SCPI string are the inherited production implementations.
    """

    @classmethod
    def from_params(cls, params: Keysight53220AParams) -> "FakeCounter":  # type: ignore[override]
        assert isinstance(params, FakeCounterParams)
        dep = FakeVisaDep(params.virtual_counter())
        if not params.detector_name:
            # Unlike a rack, a counter alone on a detector is never a working
            # setup: nothing can bias a private detector, so every gate returns
            # zero. Silent zeros read as "the detector is dead", which is the
            # wrong thing to go looking for.
            logger.warning(
                "Simulated counter %s has no detector_name, so it watches a private "
                "detector that nothing can bias and every count will be zero. Set "
                "detector_name to match the fake900 driving the device.",
                params.ip_address,
            )
        else:
            logger.info(
                "Opened simulated counter %s on detector %r",
                params.ip_address,
                params.detector_name,
            )
        return cls(dep, params)

    @property
    def virtual(self) -> VirtualKeysightCounter:
        """The emulated counter behind this driver, for tests to inspect."""
        dep = self._dep
        assert isinstance(dep, FakeVisaDep)
        return dep.counter
