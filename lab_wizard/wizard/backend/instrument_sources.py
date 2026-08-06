"""Every place this workspace can get an instrument from, in one answer.

Measurement authoring previously offered exactly one surface — this workspace's
own config tree — which made a satellite workspace useless: its tree is empty,
so there was nothing to pick. This enumerates all three kinds of source and
hands the UI a uniform structure to render.

The three kinds are distinguished by **how the source is reached**, not by what
it contains, because that is what decides authority
(:mod:`lab_wizard.lib.server.peer`):

``local``
    This workspace's ``config/instruments``. A full tree, editable here, and the
    only source whose params are copied into a generated project.

``machine`` — another workspace's daemon on this computer, reached over ``ipc://``
    A full tree, fetched with ``tree_get`` and rendered with *that server's*
    ``schema_get`` vocabulary. Editable, because reaching its ipc socket already
    proves same-machine.

``remote`` — a server on another computer, reached over ``tcp://``
    A **flat list of named leaves** and nothing more. Not a tree: a remote peer
    gets read + call, never reconfiguration, so a hierarchy it cannot act on
    would be a UI that promises what the wire refuses.

Unreachable sources are reported, not raised: a lab where one rack is off must
still be able to author measurements against the others.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from lab_wizard.lib.client.server_registry import (
    list_local_servers,
    local_server_endpoints,
)
from lab_wizard.lib.client.session import Session


logger = logging.getLogger("lab_wizard.wizard.backend.instrument_sources")

__all__ = [
    "LOCAL",
    "list_instrument_sources",
    "attributes_for_source",
    "ensure_source_registered",
    "resolve_source_url",
]

LOCAL = "local"

# Authoring is interactive; a rack that does not answer promptly is reported as
# unreachable rather than blocking the page.
_TIMEOUT_MS = 4_000


def _workspace_name(path: Optional[str]) -> str:
    if not path:
        return "server"
    return Path(path).name or "server"


def _dial_form(url: str) -> str:
    """Normalize a URL for comparison.

    A server binds ``0.0.0.0`` to accept anyone but is dialled on loopback, so
    the same endpoint has two spellings; comparing them raw would treat one
    server as two.
    """
    return url.replace("://0.0.0.0:", "://127.0.0.1:").replace("://*:", "://127.0.0.1:")


def _unique(name: str, taken: set[str]) -> str:
    """A source name not already in use.

    Source names end up in a project's ``instrument_sources``, so two workspaces
    called ``lab`` on one machine must not collapse into one routing entry.
    """
    if name not in taken:
        taken.add(name)
        return name
    for n in range(2, 100):
        candidate = f"{name}-{n}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise ValueError(f"Could not find a free source name for {name!r}")


# --------------------------- the local source ---------------------------


def _local_source(config_dir: str) -> dict[str, Any]:
    from lab_wizard.lib.server.registry import InstrumentRegistry
    from lab_wizard.lib.utilities.config_io import get_configured_tree
    from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata

    try:
        registry = InstrumentRegistry.from_config_dir(config_dir)
        attributes = registry.list_descriptions()
    except Exception as exc:  # noqa: BLE001 - a broken local config is a real answer
        logger.warning("Could not index the local instrument tree: %s", exc)
        attributes = []

    return {
        "name": LOCAL,
        "kind": LOCAL,
        "label": "This workspace",
        "url": None,
        "config_dir": config_dir,
        "tree": get_configured_tree(config_dir),
        "metadata": get_instrument_metadata(),
        "attributes": attributes,
        "editable": True,
        "reachable": True,
        "error": None,
    }


# --------------------------- server-backed sources ---------------------------


def _machine_source(entry: dict[str, Any], name: str, url: str) -> dict[str, Any]:
    """A daemon on this machine: full tree, rendered with its own schema."""
    source: dict[str, Any] = {
        "name": name,
        "kind": "machine",
        "label": f"{_workspace_name(entry.get('workspace_path'))} (this machine)",
        "url": url,
        "config_dir": entry.get("config_dir"),
        "pid": entry.get("pid"),
        "tree": [],
        "metadata": {},
        "attributes": [],
        "editable": True,
        "reachable": False,
        "error": None,
    }
    try:
        session = Session(url, timeout_ms=_TIMEOUT_MS)
        try:
            tree = session.call("tree_get")
            schema = session.call("schema_get")
            source["attributes"] = session.call("list_descriptions")
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - a stopped daemon is normal
        logger.info("Instrument source %s did not answer: %s", name, exc)
        source["error"] = str(exc)
        return source

    source["tree"] = tree.get("tree", [])
    source["roots"] = tree.get("roots", {})
    source["held_roots"] = tree.get("held_roots", [])
    source["metadata"] = schema.get("instrument_metadata", {})
    source["reachable"] = True
    return source


def _remote_source(name: str, url: str) -> dict[str, Any]:
    """A server on another machine: named leaves only, no tree.

    ``list_descriptions`` is exactly the right shape — one entry per
    ``attribute_name`` with its behavior ABC — and it is all a remote peer is
    entitled to act on.
    """
    source: dict[str, Any] = {
        "name": name,
        "kind": "remote",
        "label": f"{name} (remote machine)",
        "url": url,
        "config_dir": None,
        "tree": None,
        "metadata": {},
        "attributes": [],
        "editable": False,
        "reachable": False,
        "error": None,
    }
    try:
        session = Session(url, timeout_ms=_TIMEOUT_MS)
        try:
            source["attributes"] = session.call("list_descriptions")
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - an unreachable rack is normal
        logger.info("Remote source %s did not answer: %s", name, exc)
        source["error"] = str(exc)
        return source

    source["reachable"] = True
    return source


# --------------------------- enumeration ---------------------------


def list_instrument_sources(config_dir: str | Path) -> dict[str, Any]:
    """Local tree, same-machine daemons, and registered remote servers.

    This workspace's *own* daemon is deliberately not offered as a separate
    source — it serves the very tree already shown as ``local``, and listing it
    twice would ask the user to choose between two names for one instrument. It
    is reported separately as ``own_server`` so a transport conflict on a local
    selection can offer routing through it as the fix.
    """
    from lab_wizard.wizard.backend.remote_servers import load_remote_servers

    # Resolved, because a server advertises its resolved config dir; comparing
    # raw strings would make this workspace's own daemon look like someone
    # else's whenever the path crosses a symlink.
    config_dir = str(Path(config_dir).expanduser().resolve())
    sources: list[dict[str, Any]] = [_local_source(config_dir)]
    taken: set[str] = {LOCAL}
    own_server: Optional[dict[str, Any]] = None
    # Every endpoint any machine-local daemon answers on, so an address-book
    # entry naming one of them is recognised as the same server.
    on_this_machine: set[str] = set()

    for entry in list_local_servers():
        endpoints = local_server_endpoints(entry)
        if not endpoints:
            continue
        on_this_machine.update(_dial_form(e) for e in endpoints)
        # ipc first (see local_server_endpoints) — the endpoint that grants
        # edit rights and needs no port.
        url = endpoints[0]
        if str(Path(entry.get("config_dir", "")).expanduser().resolve()) == config_dir:
            own_server = {
                "name": _unique(_workspace_name(entry.get("workspace_path")), taken),
                "url": url,
                "pid": entry.get("pid"),
            }
            continue
        name = _unique(_workspace_name(entry.get("workspace_path")), taken)
        sources.append(_machine_source(entry, name, url))

    for server in load_remote_servers(config_dir):
        # A daemon on this machine is already listed above, with a tree and edit
        # rights. It also lands in the address book — generation registers it
        # there so projects can resolve the name — so without this it would
        # appear a second time, as a strictly worse read-only copy of itself.
        if _dial_form(server["url"]) in on_this_machine:
            logger.debug(
                "Address-book entry %s points at a daemon on this machine; "
                "already listed as a same-machine source",
                server["name"],
            )
            continue
        sources.append(_remote_source(_unique(server["name"], taken), server["url"]))

    return {"sources": sources, "own_server": own_server}


# --------------------------- generation-time lookup ---------------------------


def resolve_source_url(config_dir: str | Path, name: str) -> str:
    """URL for a source name, checked at generation time rather than trusted.

    The picker sent this name, but the page may have been open for a while — a
    daemon can have stopped, or its workspace been renamed. Resolving again here
    turns a stale selection into a clear message instead of a project that
    cannot run.
    """
    if name == LOCAL:
        raise ValueError("The local source has no URL")

    listing = list_instrument_sources(config_dir)
    own = listing.get("own_server")
    if own and own["name"] == name:
        return own["url"]
    for source in listing["sources"]:
        if source["name"] == name:
            if not source["url"]:
                raise ValueError(f"Source {name!r} has no URL to connect to")
            return source["url"]
    raise ValueError(
        f"No instrument source named {name!r} is available now. It may have "
        "stopped since this page was loaded."
    )


def ensure_source_registered(config_dir: str | Path, name: str, url: str) -> str:
    """Make ``name`` resolvable from ``config/remote/servers.yaml``.

    A generated project records *names*, not URLs, and resolves them through the
    address book at run time — so a project stays readable and portable rather
    than hard-coding a socket path. Registering here means the user never has to
    type an address for a server the wizard already discovered.

    Returns the name actually registered. An existing entry pointing somewhere
    else is never rewritten — another project may depend on it — so a suffixed
    name is allocated instead, and that is what the caller must record.
    """
    from lab_wizard.wizard.backend.remote_servers import (
        add_remote_server,
        load_remote_servers,
    )

    existing = {s["name"]: s["url"] for s in load_remote_servers(config_dir)}
    if existing.get(name) == url:
        return name

    chosen = name
    if name in existing:
        for n in range(2, 100):
            candidate = f"{name}-{n}"
            if existing.get(candidate) == url:
                return candidate
            if candidate not in existing:
                chosen = candidate
                break
        else:
            raise ValueError(f"Could not find a free address-book name for {name!r}")

    add_remote_server(config_dir, chosen, url)
    logger.info("Registered instrument source %s -> %s", chosen, url)
    return chosen


def attributes_for_source(config_dir: str | Path, name: str) -> list[dict[str, Any]]:
    """Named leaves a source currently offers, for validating a selection."""
    listing = list_instrument_sources(config_dir)
    for source in listing["sources"]:
        if source["name"] == name:
            if not source["reachable"]:
                raise ValueError(
                    f"Instrument source {name!r} is not reachable: "
                    f"{source.get('error') or 'no answer'}"
                )
            return source["attributes"]
    raise ValueError(f"No instrument source named {name!r} is available now.")
