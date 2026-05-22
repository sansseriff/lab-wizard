"""Vocabulary for declaring how instrument methods influence safety state.

An instrument opts into the server's permission state machine by declaring a
``_state_methods_`` class attribute mapping a method name to a
``(state_key, value_spec)`` tuple:

    from lab_wizard.lib.instruments.general.state_effects import Arg

    class Sim928(Child, VSource):
        _state_methods_ = {
            "set_voltage": ("voltage", Arg(0)),  # record args[0] under "voltage"
            "turn_on":     ("output",  "on"),    # record the literal "on"
            "turn_off":    ("output",  "off"),
        }

``value_spec`` is one of:
    - ``Arg(i)``      record the i-th positional argument
    - ``Kwarg(name)`` record the named keyword argument
    - ``Result()``    record the method's return value
    - any other value record it as a literal

This module is deliberately dependency-free and lives in the instrument layer,
so instruments never import server code. The server's permission gate imports
these helpers, not the other way around.
"""

from __future__ import annotations

from typing import Any


class Arg:
    """Record the i-th positional argument as the state value."""

    def __init__(self, index: int) -> None:
        self.index = index


class Kwarg:
    """Record the named keyword argument as the state value."""

    def __init__(self, name: str) -> None:
        self.name = name


class Result:
    """Record the method's return value as the state value."""


def resolve_state_value(
    spec: Any, args: list[Any], kwargs: dict[str, Any], result: Any
) -> Any:
    """Compute the value to store from a value spec and the call data."""
    if isinstance(spec, Arg):
        return args[spec.index] if spec.index < len(args) else None
    if isinstance(spec, Kwarg):
        return kwargs.get(spec.name)
    if isinstance(spec, Result):
        return result
    return spec  # literal


def collect_state_methods(cls: type) -> dict[str, tuple[str, Any]]:
    """Merge ``_state_methods_`` declarations across a class's MRO.

    Walks base -> derived so a subclass overrides individual entries by method
    name while inheriting the rest. This lets a behavior ABC (e.g. ``VSource``)
    declare the general case once, and a subclass override only the methods
    whose semantics differ (e.g. ``Dac4DChannel`` redefining ``turn_on`` /
    ``turn_off`` while still inheriting ``set_voltage``).

    Each class's *own* declaration is read via ``__dict__`` so inherited copies
    aren't double-counted.
    """
    merged: dict[str, tuple[str, Any]] = {}
    for klass in reversed(cls.__mro__):  # base first, so derived wins per key
        own = klass.__dict__.get("_state_methods_")
        if own:
            merged.update(own)
    return merged
