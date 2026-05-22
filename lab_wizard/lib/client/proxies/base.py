"""Base proxy mixin and a fallback ``RemoteOpaque`` proxy.

A proxy's class hierarchy is ``RemoteX(behavior_abc, RemoteProxy)`` — e.g.
``class RemoteVSource(VSource, RemoteProxy)``. The behavior ABC supplies the
type identity (``isinstance(p, VSource)`` succeeds and pyright uses the ABC's
signatures), and ``RemoteProxy.__init_subclass__`` walks the MRO at class
creation time and **auto-generates a forwarder for every abstract method**
inherited from any ABC base. Subclasses therefore reduce to a one-line
``class RemoteVSource(VSource, RemoteProxy): pass`` — adding a new abstract
method to a behavior ABC requires no proxy code change.

Instrument-specific (non-ABC) methods are caught by ``__getattr__`` and
forwarded reflectively, so calls like ``p.set_pid_p(0.5)`` work too.

Concrete methods inherited from the ABC are *not* auto-forwarded — they run
their normal Python body, which typically calls abstract methods that *are*
forwarders. For example, ``VSense.measure()`` calls ``self.get_voltage()``;
on the proxy, ``get_voltage`` is a forwarder, so ``measure()`` works
correctly with one RPC hop. If you need a single RPC for a concrete-on-ABC
method (skipping decomposition), override it explicitly on the proxy.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from lab_wizard.lib.client.session import Session


def _make_remote_forwarder(method_name: str) -> Callable[..., Any]:
    """Build a method body that forwards ``(args, kwargs)`` over the wire."""

    def forwarder(self: "RemoteProxy", *args: Any, **kwargs: Any) -> Any:
        return self._call_remote(method_name, *args, **kwargs)

    forwarder.__name__ = method_name
    forwarder.__qualname__ = f"<auto-proxy>.{method_name}"
    return forwarder


class RemoteProxy:
    """Mixin that gives a proxy its session and ``inst://...`` path, and
    auto-forwards every abstract method inherited from a behavior ABC.

    A subclass like ``RemoteVSource(VSource, RemoteProxy)`` automatically
    gets a forwarder for each abstract method declared on ``VSource``. Adding
    a new abstract method to a behavior ABC requires no proxy code change.

    Subclasses may still override specific methods for custom behavior
    (e.g. local-side caching, client-side argument validation) — explicit
    overrides defined in the subclass body take precedence over the
    auto-generated forwarders.
    """

    _session: "Session"
    _inst_path: str

    def __init__(self, session: "Session", inst_path: str) -> None:
        # Use object.__setattr__ to bypass any __setattr__ overridden by
        # subclasses that wire typed fields via descriptors.
        object.__setattr__(self, "_session", session)
        object.__setattr__(self, "_inst_path", inst_path)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        # Union of every abstract method visible on this class's MRO.
        abstract_names: set[str] = set()
        for base in cls.__mro__:
            abstract_names |= set(getattr(base, "__abstractmethods__", ()))
        # Inject a forwarder for each abstract method the subclass didn't
        # explicitly override. Explicit overrides win.
        for name in abstract_names:
            if name not in cls.__dict__:
                setattr(cls, name, _make_remote_forwarder(name))
        # All abstract methods are now satisfied at runtime; clear the
        # abstract marker so the class is instantiable. (ABCMeta computed
        # __abstractmethods__ from the original namespace; setattr above
        # doesn't trigger a recomputation, so we have to reset it.)
        cls.__abstractmethods__ = frozenset()

    def _call_remote(self, method: str, *args: Any, **kwargs: Any) -> Any:
        return self._session.call_inst(self._inst_path, method, list(args), dict(kwargs))

    def __getattr__(self, name: str) -> Callable[..., Any]:
        """Reflective fallback: any unknown attribute becomes a remote method call.

        Only invoked when normal attribute lookup fails — so methods explicitly
        defined on the proxy or its ABC (including auto-forwarders) take
        precedence. Leading-underscore names are blocked so internal Python
        protocols (``__copy__``, ``__getstate__``, ...) don't accidentally
        fire RPCs.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def remote_method(*args: Any, **kwargs: Any) -> Any:
            return self._call_remote(name, *args, **kwargs)

        remote_method.__name__ = name
        remote_method.__qualname__ = f"{type(self).__name__}.{name}"
        return remote_method

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._inst_path}>"


class RemoteOpaque(RemoteProxy):
    """Proxy for instruments that don't match a known behavior ABC.

    Has no behavior ABC parent, so ``__init_subclass__`` injects nothing.
    All method calls go through ``__getattr__`` reflective dispatch.
    """
