"""Resolve a measurement's instruments from several sources at once.

``--remote <url>`` replaces a project's *entire* resource source, so a project
is all-local or all-remote. That forbids the ordinary case: the DBay rack is
reachable from anywhere (its GUI backend multiplexes), while the Prologix bus is
held by one machine's server. Wanting one instrument locally and another through
a server is not exotic.

This routes per attribute instead. It works because ``ResourceConfig`` and
``RemoteResources`` already expose an identical ``from_attribute(name)`` — a
deliberate symmetry, documented in ``remote_resources``. A composite is then
little more than an owner lookup.

Ownership is recorded **in the project YAML**, not passed on the command line:

    resources:
      instrument_sources:
        bias_vsource: local
        counter: cryo-rack          # a name in config/remote/servers.yaml

so a project runs the same way for everyone, is inspectable, and is
reproducible six months later — none of which is true of a flag someone has to
remember. An attribute with no entry defaults to ``local``, which keeps every
existing project working untouched.

**Savers and plotters never route.** Only instruments have a location; a saver
writes to this machine's database and a plotter draws on this machine's screen,
so both are served from the local project regardless of where the instruments
live. They are exposed here as passthroughs because generated setup code says
``DatabaseSaver.from_config(resources, key=...)`` against whatever resource
source it was handed — without them, every project that routes an instrument
anywhere would die on ``exp.savers[key]``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Type, TypeVar, overload


logger = logging.getLogger(__name__)

T = TypeVar("T")

LOCAL = "local"

__all__ = ["CompositeResources", "LOCAL"]


class CompositeResources:
    """Dispatches ``from_attribute`` to whichever source owns the attribute."""

    def __init__(
        self,
        local: Any = None,
        remotes: Optional[dict[str, Any]] = None,
        sources: Optional[dict[str, str]] = None,
        default_source: str = LOCAL,
    ) -> None:
        self._local = local
        self._remotes = dict(remotes or {})
        self._sources = dict(sources or {})
        # Where an attribute with no explicit entry comes from. ``local`` for a
        # normal project — which is why existing projects need no mapping — and
        # a server name for the ``--remote`` override, where *everything* is
        # remote but savers and plotters must still resolve locally.
        self._default_source = default_source
        self._cache: dict[str, Any] = {}

    # ------------------------- construction -------------------------

    @classmethod
    def from_project(
        cls,
        project: Any,
        *,
        server_urls: Optional[dict[str, str]] = None,
        connect: Optional[Any] = None,
    ) -> "CompositeResources":
        """Build from a project's own ``instrument_sources`` mapping.

        ``server_urls`` maps a source name to a URL — normally the address book
        in ``config/remote/servers.yaml``. Only servers a project actually
        references are connected, so an unused entry in the address book costs
        nothing and an unreachable one is not an error unless needed.
        """
        sources = _project_sources(project)
        needed = {name for name in sources.values() if name != LOCAL}

        connect_fn = connect or _default_connect
        remotes: dict[str, Any] = {}
        for name in sorted(needed):
            url = (server_urls or {}).get(name)
            if not url:
                raise ValueError(
                    f"This project routes instruments to server {name!r}, but no "
                    "such server is registered. Add it on the Remote Servers "
                    "page (config/remote/servers.yaml)."
                )
            remotes[name] = connect_fn(url)
            logger.info("Connected to %s (%s) for this project", name, url)

        return cls(local=project.resources, remotes=remotes, sources=sources)

    @classmethod
    def all_remote(
        cls,
        project: Any,
        url: str,
        *,
        name: str = "remote",
        connect: Optional[Any] = None,
    ) -> "CompositeResources":
        """Route every *instrument* through one server, as ``--remote`` means.

        Not the same as handing the project a bare ``RemoteResources``: savers
        and plotters have no remote counterpart and must still come from the
        local project, which is exactly what a composite with a remote default
        gives us. Using one class for both routing modes also means generated
        code has a single shape.
        """
        connect_fn = connect or _default_connect
        return cls(
            local=getattr(project, "resources", None),
            remotes={name: connect_fn(url)},
            sources={},
            default_source=name,
        )

    # ------------------------- local-only resources -------------------------

    @property
    def savers(self) -> Any:
        """Saver params from the local project. Never routed — see the module docstring."""
        return self._local_section("savers")

    @property
    def plotters(self) -> Any:
        """Plotter params from the local project. Never routed."""
        return self._local_section("plotters")

    def _local_section(self, name: str) -> Any:
        section = getattr(self._local, name, None)
        if section is None:
            raise ValueError(
                f"This project has no local {name}. Savers and plotters are always "
                "resolved locally, even when every instrument is routed to a server."
            )
        return section

    # ------------------------- attribute API -------------------------

    @overload
    def from_attribute(self, name: str) -> Any: ...

    @overload
    def from_attribute(self, name: str, as_type: Type[T]) -> T: ...

    def from_attribute(self, name: str, as_type: Optional[Type[Any]] = None) -> Any:
        """Return the instrument (or proxy) for ``name`` from its owner."""
        if name in self._cache:
            return self._cache[name]

        source = self._sources.get(name, self._default_source)
        if source == LOCAL:
            if self._local is None:
                raise ValueError(
                    f"Attribute {name!r} is local, but this project has no local "
                    "resource tree."
                )
            resolved = self._local.from_attribute(name)
        else:
            remote = self._remotes.get(source)
            if remote is None:
                raise ValueError(
                    f"Attribute {name!r} is routed to server {source!r}, which is "
                    "not connected."
                )
            resolved = remote.from_attribute(name)

        self._cache[name] = resolved
        return resolved

    def source_of(self, name: str) -> str:
        """Which source owns ``name`` — for diagnostics and display."""
        return self._sources.get(name, self._default_source)

    # ------------------------- lifecycle -------------------------

    def close(self) -> None:
        for remote in self._remotes.values():
            close = getattr(remote, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - teardown is best-effort
                    logger.debug("Closing a remote source failed", exc_info=True)

    def __enter__(self) -> "CompositeResources":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()


def _project_sources(project: Any) -> dict[str, str]:
    """``{attribute_name: source}`` declared by a project, if any."""
    resources = getattr(project, "resources", None)
    raw = getattr(resources, "instrument_sources", None) or {}
    return {str(k): str(v) for k, v in raw.items()}


def _default_connect(url: str) -> Any:
    from lab_wizard.lib.client.remote_resources import RemoteResources

    return RemoteResources.connect(url)
