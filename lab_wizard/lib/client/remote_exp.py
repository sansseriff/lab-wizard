"""RemoteExp — client-side mirror of ``Exp`` for remote projects.

A measurement setup file that wants to use a remote tree replaces

    exp = load_exp_from_yaml(project_yaml)
    bias_vsource = exp.from_attribute("bias_vsource")     # VSource

with

    exp = RemoteExp.connect("tcp://lab-server:12300")
    bias_vsource = exp.from_attribute("bias_vsource")     # RemoteVSource (a VSource)

Nothing else changes. The measurement class consumes objects via the behavior
ABCs (``VSource``, ``VSense``) and proxies satisfy those.

Phase 2 surface:
    RemoteExp.connect(url) -> RemoteExp
    RemoteExp.from_attribute(name) -> proxy
    RemoteExp.list_attributes() -> list[str]
    RemoteExp.describe_attribute(name) -> dict
    RemoteExp.close()

There is no client-side YAML, no client-side instrument-config tree, and no
``from_config`` — the server owns config and addressing, the client only
references named leaves.
"""

from __future__ import annotations

from typing import Any, Optional, Type, TypeVar, overload

from lab_wizard.lib.client.proxies.base import RemoteProxy
from lab_wizard.lib.client.proxies.registry import proxy_class_for
from lab_wizard.lib.client.session import Session


T = TypeVar("T")


class RemoteExp:
    """Connection to a remote lab_wizard server."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._proxy_cache: dict[str, RemoteProxy] = {}

    @classmethod
    def connect(cls, url: str, *, timeout_ms: int = 10_000) -> "RemoteExp":
        return cls(Session(url, timeout_ms=timeout_ms))

    # ------------------------- attribute API -------------------------

    @overload
    def from_attribute(self, name: str) -> Any: ...

    @overload
    def from_attribute(self, name: str, as_type: Type[T]) -> T: ...

    def from_attribute(self, name: str, as_type: Optional[Type[Any]] = None) -> Any:
        """Return a typed proxy for the leaf the server has named ``name``.

        Without ``as_type``: result type is whichever behavior-ABC proxy
        matches what the server reports (``RemoteVSource``, ``RemoteVSense``,
        …) or ``RemoteOpaque`` if no ABC matches.

        With ``as_type``: same proxy is returned at runtime, but pyright/Mypy
        treat the result as if it were that type. This is the canonical
        ``typing.cast`` pattern packaged into the API: useful when you want
        ctrl-click navigation and autocomplete to point at the concrete
        server-side class (e.g. ``Sim928``, ``Dac4DChannel``) rather than the
        ABC. The runtime object is still a proxy — ``as_type`` is a static
        hint for tooling, not a runtime guarantee.

        Example:
            from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928
            bias = exp.from_attribute("bias", Sim928)
            bias.set_voltage(0.5)   # ctrl-click jumps to Sim928.set_voltage
        """
        _ = as_type  # consumed only by the overload signatures
        if name in self._proxy_cache:
            return self._proxy_cache[name]
        info = self._session.call("describe_attribute", {"name": name})
        proxy_cls = proxy_class_for(info.get("behavior_abc"))
        proxy = proxy_cls(self._session, info["path"])
        self._proxy_cache[name] = proxy
        return proxy

    def list_attributes(self) -> list[str]:
        return sorted(self._session.call("list_attributes").keys())

    def describe_attribute(self, name: str) -> dict[str, Any]:
        return self._session.call("describe_attribute", {"name": name})

    def list_descriptions(self) -> list[dict[str, Any]]:
        return self._session.call("list_descriptions")

    # ------------------------- lifecycle -------------------------

    @property
    def session(self) -> Session:
        return self._session

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "RemoteExp":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()
