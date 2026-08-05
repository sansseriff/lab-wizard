"""Behavior interfaces, and how the rest of the system learns about them.

A *behavior* is the abstract capability a measurement asks for — ``VSource``,
``VSense``, ``Counter``. It is the contract that makes a measurement portable:
an IV curve needs *a* voltage source, not a Sim928, and the same measurement
runs against a local instrument or a network proxy because both satisfy the
same ABC.

Three separate places need to agree on what the behaviors are:

* the server, to answer ``describe_attribute`` with a ``behavior_abc`` name,
* the client, to pick the matching proxy class for that name,
* the wizard, to offer an instrument for a requirement of that type.

That agreement used to be two hand-written lists in two packages, joined by
bare strings. They had already drifted — ``ChannelProvider`` was in one and
commented out in the other — and the failure was silent: an unlisted behavior
reports ``None``, which reads as "this instrument does nothing in particular"
and quietly removes it from every picker.

So a behavior registers itself:

    class Counter(InstrumentBehavior, specificity=TERMINAL):
        ...

and everything else is derived. Names come from ``cls.__name__``, so there is
no string to mistype, and the client keys its proxy table on the ABC *class*.

**Why import-time registration is sufficient.** Nothing scans the filesystem
here. A behavior is registered when its module is imported, and classification
only ever asks about a class we already hold — which cannot exist without its
bases having been imported. So by the time we ask "is this a Counter?", any
Counter in the tree has already registered. The invariant is structural rather
than something a loader has to remember.

**Why specificity is declared, not positional.** Matching returns the first ABC
an object satisfies, so an instrument implementing two behaviors depends on the
comparison order. Leaving that to source order — or worse, to a directory
listing — would let one instrument report different behaviors on two machines,
and the client picks its proxy class from that answer. A declared rank keeps it
reproducible, which is the same reason transport declarations are explicit
rather than inferred.
"""

from __future__ import annotations

from abc import ABC
from typing import Any, Iterable


__all__ = [
    "InstrumentBehavior",
    "TERMINAL",
    "CONTAINER",
    "register_behavior",
    "behaviors",
    "behavior_name_for",
]


# A behavior an instrument *is* — what a measurement binds to. Checked first, so
# a channel that both sources voltage and provides sub-channels reports the
# capability a measurement can actually use.
TERMINAL = 100
# A structural behavior: it holds other instruments rather than doing something
# itself. Only meaningful when nothing more specific matches.
CONTAINER = 10


_REGISTERED: dict[type, int] = {}


def register_behavior(cls: type, specificity: int) -> type:
    """Record ``cls`` as a behavior interface. Usually via the class kwarg."""
    existing = _REGISTERED.get(cls)
    if existing is not None and existing != specificity:
        raise ValueError(
            f"Behavior {cls.__name__} is already registered with specificity "
            f"{existing}; refusing to re-register it as {specificity}."
        )
    _REGISTERED[cls] = specificity
    return cls


class InstrumentBehavior(ABC):
    """Base for behavior interfaces. Subclasses register by passing ``specificity``.

    Only classes that pass the keyword are registered, so concrete instruments
    inheriting a behavior (``Sim928(VSource)``) and stand-ins do not become
    behaviors themselves.
    """

    def __init_subclass__(cls, *, specificity: int | None = None, **kwargs: Any) -> None:
        # Chained, not terminal: a proxy is ``RemoteVSource(VSource, RemoteProxy)``
        # and RemoteProxy's own __init_subclass__ generates its forwarders.
        super().__init_subclass__(**kwargs)
        if specificity is not None:
            register_behavior(cls, specificity)


def behaviors() -> tuple[tuple[str, type], ...]:
    """Registered behaviors as ``(name, class)``, most specific first.

    Ties break on name so the order is stable across processes — two behaviors
    at the same rank must not swap places between the server that reports a name
    and the client that resolves it.
    """
    ordered = sorted(_REGISTERED, key=lambda cls: (-_REGISTERED[cls], cls.__name__))
    return tuple((cls.__name__, cls) for cls in ordered)


def behavior_name_for(obj_or_cls: Any, *, is_class: bool = False) -> str | None:
    """Name of the most specific behavior ``obj_or_cls`` satisfies, if any."""
    for name, abc in behaviors():
        try:
            if issubclass(obj_or_cls, abc) if is_class else isinstance(obj_or_cls, abc):
                return name
        except TypeError:
            continue
    return None


def registered_behaviors() -> Iterable[type]:
    """The behavior classes themselves — used by coverage checks."""
    return tuple(_REGISTERED)
