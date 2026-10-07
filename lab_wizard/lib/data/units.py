"""SI prefixes on a known unit: ``100 kΩ`` is ``100000.0 Ω``.

A setup field is a quantity, ``{value: 100, unit: kΩ}``. Facets compare such
fields in their base unit, so a range over ``bias_resistor`` sees 100 kΩ and
100000 Ω as one value, and ``setup("bias_resistance")`` reads it in the unit
the procedure declared. That is all this does: SI prefixes on one base unit.
A unit it does not know converts only to itself.
"""

from __future__ import annotations

__all__ = ["UnitError", "base_unit", "convert", "in_base", "to_base"]

# Spellings of one unit, as people type them.
_ALIASES = {
    "ohm": "Ω", "ohms": "Ω", "Ohm": "Ω", "Ohms": "Ω", "Ω": "Ω", "\u2126": "Ω",
    "volt": "V", "volts": "V",
    "amp": "A", "amps": "A",
    "watt": "W", "watts": "W",
    "sec": "s",
}
# Units a prefix may stand in front of.
_BASES = {"Ω", "V", "A", "W", "Hz", "s", "K", "F", "H", "m", "g", "S", "J", "C", "T", "Pa"}
_PREFIXES = {
    "T": 1e12, "G": 1e9, "M": 1e6, "k": 1e3,
    "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15,
}


class UnitError(ValueError):
    """Two units that are not the same base unit with different prefixes."""


def _canonical(unit: str) -> str:
    unit = unit.strip()
    if unit in _ALIASES:
        return _ALIASES[unit]
    # A prefix on a spelled-out unit: kohm, Mohm.
    if len(unit) > 1 and unit[0] in _PREFIXES and unit[1:] in _ALIASES:
        return unit[0] + _ALIASES[unit[1:]]
    return unit


def to_base(unit: str) -> tuple[float, str]:
    """``(factor, base)``: ``kΩ`` is ``(1000.0, "Ω")``. An unknown unit is its own base."""
    unit = _canonical(unit)
    if unit in _BASES:
        return 1.0, unit
    if len(unit) > 1 and unit[0] in _PREFIXES and unit[1:] in _BASES:
        return _PREFIXES[unit[0]], unit[1:]
    return 1.0, unit


def base_unit(unit: str | None) -> str | None:
    """The unit ``unit``'s values are compared in: ``kΩ`` → ``Ω``."""
    return to_base(unit)[1] if unit else unit


def _tidy(value: float) -> float:
    # 4.7 kΩ is 4700.0 Ω, not 4700.000000000001: twelve significant digits is
    # more than any setup field is known to.
    return float(f"{value:.12g}")


def in_base(value: float, unit: str | None) -> float:
    """``value`` in ``unit``'s base unit: ``in_base(4.7, "kΩ")`` is ``4700.0``."""
    return _tidy(float(value) * to_base(unit)[0]) if unit else float(value)


def convert(value: float, unit: str | None, target: str | None) -> float:
    """``value`` in ``unit``, expressed in ``target``. ``UnitError`` across base units.

    No unit on either side converts nothing: a bare number is taken as it is.
    """
    if not unit or not target:
        return float(value)
    (have, have_base), (want, want_base) = to_base(unit), to_base(target)
    if have_base != want_base:
        raise UnitError(f"{unit} cannot be expressed in {target}")
    return _tidy(float(value) * have / want)
