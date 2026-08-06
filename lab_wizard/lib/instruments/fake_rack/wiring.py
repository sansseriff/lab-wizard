"""Which simulated instruments are connected to the same simulated detector.

A rack shares one :class:`~lab_wizard.lib.instruments.fake_rack.snspd.SnspdModel`
because a mainframe owns it and its modules sit in its slots — the object *is*
the wiring, and that works as long as everything wired together lives under one
root in the config tree.

A counter does not. The real one is a separate box on Ethernet, so in the tree
it is a second root, and there is no parent to hand both it and the bias source
the same detector. Yet a PCR curve is exactly the measurement where they must
share one: the bias the source applies is what decides the count rate the
counter reports. Without a shared model the counter would report counts for a
detector nobody is biasing.

So the connection is named. Two params entries that declare the same
``detector_name`` are handed the same live model, which is the config-tree
equivalent of running a coax from the rack to the counter's front panel. The
name is visible in the project YAML, so a simulated setup states its wiring
rather than implying it.

An empty name means "not wired to anything else" and yields a private detector
per instrument — the default, so an instrument that says nothing about wiring
never silently shares state with another.

Lifetime is process-global, like the hardware it stands for: the detector
outlives any one instrument object, and reopening a rack finds it in whatever
state it was left in. :func:`reset_detectors` exists for tests that want a cold
lab rather than yesterday's.
"""

from __future__ import annotations

import logging

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel, SnspdModelParams

logger = logging.getLogger("lab_wizard.lib.instruments.fake_rack.wiring")

_DETECTORS: dict[str, SnspdModel] = {}


def shared_detector(name: str, params: SnspdModelParams) -> SnspdModel:
    """The named detector, created from ``params`` the first time it is asked for.

    A later caller naming the same detector with different constants keeps the
    existing model and is warned: the two config entries disagree about one
    piece of hardware, and silently honouring the last one to load would make
    the simulated physics depend on instantiation order.
    """
    if not name:
        return SnspdModel(params)

    existing = _DETECTORS.get(name)
    if existing is None:
        _DETECTORS[name] = SnspdModel(params)
        logger.debug("Created simulated detector %r", name)
        return _DETECTORS[name]

    if existing.params != params:
        logger.warning(
            "Simulated detector %r is already defined with different constants; "
            "keeping the existing ones. Two config entries describe one detector "
            "and disagree about it.",
            name,
        )
    return existing


def reset_detectors() -> None:
    """Forget every shared detector, so the next request builds a fresh one."""
    _DETECTORS.clear()
