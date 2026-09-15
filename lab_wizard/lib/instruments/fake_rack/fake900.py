"""FAKE900 — a simulated SIM900 mainframe, and the detector it is wired to.

Structurally identical to the real mainframe: a hybrid node that is a child of
a GPIB controller and a parent of slot modules, handing each module a
slot-scoped dependency. The one thing it owns that a real mainframe does not is
``device`` — the :class:`SnspdModelParams` describing the detector this rack is
connected to.

The detector lives here rather than on the bus or on a module because that is
where the wiring is: a voltage source in one slot and a voltmeter in another
are connected to *the same* detector, and one shared
:class:`~lab_wizard.lib.instruments.fake_rack.snspd.SnspdModel` per mainframe
is exactly that circuit. Two mainframes on one fake bus are two independent
detectors.

Anything wired to this detector from *outside* the rack — a simulated counter
on its own transport, say — cannot be reached by ownership, so it joins by name
instead: see :mod:`lab_wizard.lib.instruments.fake_rack.wiring` and the
``detector_name`` field below.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, SerializeAsAny

from lab_wizard.lib.instruments.fake_rack.children import (
    Fake900ModuleParams,
    FakeGpibChildParams,
)
from lab_wizard.lib.instruments.fake_rack.snspd import SnspdModelParams
from lab_wizard.lib.instruments.fake_rack.wiring import shared_detector
from lab_wizard.lib.instruments.fake_rack.virtual_rack import (
    VirtualGpibDevice,
    VirtualSim900,
    VirtualSlotModule,
)
from lab_wizard.lib.instruments.general.discovery import (
    DiscoveryAction,
    NoParams,
    SelfCandidate,
    SelfCandidatesResult,
)
from lab_wizard.lib.instruments.general.parent_child import (
    Discoverable,
    GPIBAddressLike,
    ParentParams,
)
from lab_wizard.lib.instruments.sim900.comm import Sim900MainframeDep
from lab_wizard.lib.instruments.sim900.sim900 import Sim900

Fake900ChildParams = Fake900ModuleParams


class Fake900Params(
    GPIBAddressLike,
    ParentParams["Fake900", Sim900MainframeDep, Fake900ChildParams],
    FakeGpibChildParams,
    Discoverable,
):
    """Parameters for the simulated mainframe (hybrid Parent + Child)."""

    children: dict[str, SerializeAsAny[Fake900ModuleParams]] = Field(default_factory=dict)
    type: Literal["fake900"] = "fake900"
    device: SnspdModelParams = Field(
        default_factory=SnspdModelParams,
        description="The simulated detector this mainframe's modules are wired to",
    )
    detector_name: str = Field(
        default="",
        description=(
            "Name the detector to share it with instruments outside this rack "
            "(e.g. a fake_counter reading the same device); empty keeps it private"
        ),
    )

    def children_claimable(self) -> bool:
        # Mirrors Sim900Params.
        return True

    @classmethod
    def resource_class(cls):
        return Fake900

    # -- Simulation ---------------------------------------------------------

    def virtual_device(self) -> VirtualGpibDevice:
        """Build the emulated mainframe this params subtree describes.

        Slots come from the children the config declares, so a project that
        selected only a voltage source gets a rack with only that module —
        addressing the empty slot then goes unanswered, as it would on real
        hardware.

        The detector is private unless ``detector_name`` names it, in which case
        instruments outside this rack — a simulated counter, typically — can be
        wired to the same device. See
        :mod:`lab_wizard.lib.instruments.fake_rack.wiring`.
        """
        model = shared_detector(self.detector_name, self.device)
        modules: dict[int, VirtualSlotModule] = {}
        for child in self.children.values():
            builder = getattr(child, "virtual_module", None)
            if builder is None:
                continue
            modules[int(child.slot)] = builder(model)
        return VirtualSim900(modules, model)

    # -- Discovery ----------------------------------------------------------

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return [
            DiscoveryAction(
                name="scan_gpib",
                label="Scan Simulated GPIB Bus",
                description="Search for simulated mainframes on a fake GPIB controller",
                params_model=NoParams,
                handler=cls._scan_gpib,
                parent_dep="fakegpib",
            ),
        ]

    @classmethod
    def _scan_gpib(cls, params: NoParams, parent_inst: Any) -> SelfCandidatesResult:
        """Walk the simulated bus exactly as the real scan walks a real one.

        Same ``*IDN?`` per address through the same controller dependency; only
        the identification string differs, so a simulated rack can never be
        mistaken for hardware in the config tree.
        """
        from lab_wizard.lib.instruments.general.discovery import get_idn

        controller = parent_inst.dep

        found: list[SelfCandidate] = []
        for address in range(30):
            idn = get_idn(controller, address)
            if not idn or "FAKE900" not in idn:
                continue
            found.append(SelfCandidate(key_fields={"gpib_address": str(address)}, idn=idn))

        return SelfCandidatesResult(found=found)


class Fake900(Sim900):
    """SIM900 mainframe driver pointed at a simulated bus.

    Slot routing, child instantiation, and the ``CONN``/escape framing are all
    inherited from :class:`Sim900`; only the declared parent differs.
    """
