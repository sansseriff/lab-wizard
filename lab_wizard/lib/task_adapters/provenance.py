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

import logging
from typing import Any

from lab_wizard.lib.task_adapters.lifecycle import bound_instruments

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
    """``{name: params}`` for every instrument this run drives.

    Keyed by ``attribute_name`` — the same handle the project uses to name the
    instrument — falling back to the class name when an instrument has none.
    """
    out: dict[str, Any] = {}
    for instrument in bound_instruments(resources):
        params = instrument_params(instrument)
        if params is None:
            continue
        # A proxy answers unknown attributes with a remote call, so a name that
        # is not a string is not a name.
        name = getattr(instrument, "attribute_name", None)
        if not isinstance(name, str) or not name:
            name = type(instrument).__name__
        if name in out:  # two unnamed instruments of one type
            for n in range(2, 100):
                if f"{name}_{n}" not in out:
                    name = f"{name}_{n}"
                    break
        out[name] = params
    return out
