"""FAKE928 — a SIM928 voltage source with no SIM928 behind it.

The driver *is* :class:`Sim928`: same ``VOLT``/``OPON``/``OPOF`` commands, same
three-decimal formatting, same slot framing. Only the params type is new, so
the wizard can offer it as its own instrument and the config tree can tell a
simulated rack from a real one. What it talks to is
:class:`~lab_wizard.lib.instruments.fake_rack.virtual_rack.VirtualVoltageSource`,
which applies the voltage to the shared SNSPD model rather than to a DAC.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModel
from lab_wizard.lib.instruments.fake_rack.virtual_rack import (
    VirtualSlotModule,
    VirtualVoltageSource,
)
from lab_wizard.lib.instruments.general.parent_child import SlotLike
from lab_wizard.lib.instruments.fake_rack.children import Fake900ModuleParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928


class Fake928Params(SlotLike, Fake900ModuleParams):
    """Parameters for the simulated voltage source module.

    Mirrors :class:`~lab_wizard.lib.instruments.sim900.modules.sim928.Sim928Params`
    field for field, except that ``settling_time`` defaults to zero: there is
    no hardware to settle, and a sweep of a few hundred points should not cost
    a few hundred sleeps in a test suite. Raise it to simulate a slow rack.
    """

    type: Literal["fake928"] = "fake928"
    offline: bool | None = False
    settling_time: float | None = Field(
        default=0.0,
        description="(seconds)",
    )
    attribute_name: str | None = ""

    @classmethod
    def resource_class(cls):
        return Fake928

    def virtual_module(self, model: SnspdModel) -> VirtualSlotModule:
        """The emulated hardware this params entry stands for."""
        return VirtualVoltageSource(model)


class Fake928(Sim928):
    """SIM928 driver pointed at a simulated slot.

    Everything — command formatting, return values, the ``_state_methods_``
    declarations the permission gate reads — is inherited unchanged from
    :class:`Sim928`. Only the declared parent differs, because a simulated
    module belongs to a simulated mainframe.
    """
