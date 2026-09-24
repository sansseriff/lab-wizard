"""What the instruments were set to when a run started.

A project's YAML records the *measurement's* params — the sweep, the gate time,
the choreography a procedure takes. What it deliberately does not record is how
the instruments themselves were configured: their addresses and bench settings
live in a ``config/instruments`` tree, local or a server's, and are re-applied
at the start of every run (``apply_baseline``).

That is right for running, and thin for reading back. The config tree is edited
between runs, so months later nothing says which calibration wavelength or
which coupling a curve was taken at. This module takes that snapshot at run
start, so the answer is stored with the data rather than inferred from a file
that has since changed. See ``plans/procedure_plan.md`` 5.6.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from typing import Any

from lab_wizard.lib.instruments.general.behavior import InstrumentBehavior

logger = logging.getLogger(__name__)

__all__ = ["baseline_snapshot", "instrument_params"]


def instrument_params(instrument: Any) -> dict[str, Any] | None:
    """One instrument's configured params, local or through a server.

    Returns ``None`` when nothing can answer — a stand-in, or a server too old
    to know the RPC. Provenance is worth having, never worth failing a run for.
    """
    remote = getattr(instrument, "configured_params", None)
    if callable(remote):
        try:
            return remote()
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.warning("Could not read params for %r: %s", instrument, exc)
            return None

    params = getattr(instrument, "params", None)
    if params is None or not hasattr(params, "model_dump"):
        return None
    # ``children`` is excluded because each instrument answers for itself: a
    # rack and the module inside it both appear here when both are bound.
    return params.model_dump(mode="json", exclude={"children"})


def baseline_snapshot(resources: Any) -> dict[str, Any]:
    """``{role: {class, type, attribute_name, params}}`` for every instrument the run drives.

    Keyed by **role** (the resources field the procedure knows it by), so the
    same key means the same job in every run of a procedure, whatever
    instrument filled it. An instrument whose params cannot be read is still
    listed, by class, so a run never forgets which instruments it used. A role
    holding several instruments lists them as ``role[0]``, ``role[1]``.
    """
    if dataclasses.is_dataclass(resources) and not isinstance(resources, type):
        roles: Iterable[tuple[str, Any]] = (
            (f.name, getattr(resources, f.name)) for f in dataclasses.fields(resources)
        )
    else:
        roles = vars(resources).items()

    out: dict[str, Any] = {}
    for role, value in roles:
        items = value if isinstance(value, (list, tuple)) else (value,)
        instruments = [item for item in items if isinstance(item, InstrumentBehavior)]
        for index, instrument in enumerate(instruments):
            key = role if len(instruments) == 1 else f"{role}[{index}]"
            params = instrument_params(instrument) or {}
            # A proxy answers unknown attributes with a remote call, so a name
            # that is not a string is not a name.
            name = getattr(instrument, "attribute_name", None)
            out[key] = {
                "class": type(instrument).__name__,
                "type": params.get("type"),
                "attribute_name": name if isinstance(name, str) and name else None,
                "params": params,
            }
    return out
