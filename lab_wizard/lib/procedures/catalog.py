"""The registered instrument behaviors, for a procedure editor to offer.

What each step type takes is lab_procedure's :func:`~lab_procedure.step_catalog`;
this adds what fills a role, which only lab_wizard knows.
"""

from __future__ import annotations

import inspect
from typing import Any


__all__ = ["behavior_catalog"]


def behavior_catalog() -> dict[str, dict[str, Any]]:
    """Every registered behavior, and which behaviors an instrument of it also is.

    ``satisfies`` includes the behavior itself, so a role of behavior ``B`` can
    fill a field requiring ``A`` exactly when ``A`` is in ``satisfies[B]``.
    ``bindable`` is false for a structural behavior (``ChannelProvider``): it
    holds instruments rather than doing anything, so no role asks for one.
    """
    from lab_wizard.lib.instruments.general.behavior import TERMINAL, specificity_of
    from lab_wizard.lib.procedures.definition import _behaviors

    registered = _behaviors()
    out: dict[str, dict[str, Any]] = {}
    for name, cls in sorted(registered.items()):
        doc = inspect.getdoc(cls) or ""
        out[name] = {
            "name": name,
            "summary": doc.splitlines()[0] if doc else "",
            "satisfies": [other for other, base in registered.items() if issubclass(cls, base)],
            "bindable": (specificity_of(cls) or 0) >= TERMINAL,
        }
    return out
