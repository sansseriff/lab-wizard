"""Generic instrument discovery system.

Instrument Params classes can inherit ``Discoverable`` to expose
self-describing discovery actions (probe connections, scan buses,
populate children, etc.) that the wizard UI renders dynamically.
"""

from __future__ import annotations
from typing import Any
import time


def get_idn(controller_dep: Any, address: int) -> str:
    """Query *IDN? from a GPIB-addressed instrument.

    Args:
        controller_dep: PrologixControllerDep or similar with
            ``query_instrument(address, command)``. The controller's own
            ``read_delay_s`` governs the inter-write/read pause.
        address: GPIB address to query

    Returns:
        IDN string (empty if no response or error)
    """
    try:
        raw = controller_dep.query_instrument(address, "*IDN?")
        return (
            raw.decode(errors="replace").strip()
            if isinstance(raw, bytes)
            else str(raw).strip()
        )
    except Exception:
        return ""


class Discoverable:
    """Mixin for Params classes that support instrument discovery.

    Subclasses override ``discovery_actions`` to declare what they support
    and ``run_discovery`` to execute a named action.
    """

    @classmethod
    def discovery_actions(cls) -> list[dict[str, Any]]:
        """Return descriptors for each discovery action this type supports.

        Each descriptor is a dict with keys:
            name         - unique action identifier (e.g. "probe", "scan_usb")
            label        - short button text for the UI
            description  - longer help text
            inputs       - list of {name, type, label, default?} for UI fields
            parent_dep   - (optional) type string of the parent instrument this
                           action depends on (e.g. "prologix_gpib").  When set,
                           the backend loads the parent from config and passes it
                           as the ``parent`` kwarg to ``run_discovery``.  Manual
                           input fields for parent connection params become
                           unnecessary.
            result_type  – "probe" | "children" | "self_candidates" | "generic"
                - "probe": connection/port discovery, result has {found: [{port, description?}, ...]}
                - "children": sub-instruments found under this instrument, result has {children: [{type, key_fields, idn?}, ...]}
                - "self_candidates": instances of this instrument found on a bus, user picks one, result has {found: [{key_fields, idn?}, ...]}
                - "generic": unspecified result format
        """
        return []

    @classmethod
    def run_discovery(
        cls, action: str, params: dict[str, Any], *, parent: Any = None
    ) -> dict[str, Any]:
        """Execute a named discovery action with the given input params.

        Args:
            action: The action name to execute.
            params: Dict of user-supplied input values.
            parent: Optional live parent instrument instance, provided
                automatically by the backend when the action declares a
                ``parent_dep``.
        """
        raise NotImplementedError(f"Unknown discovery action: {action}")
