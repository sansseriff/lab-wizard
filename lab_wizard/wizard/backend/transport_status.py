"""Transport-sharing status for the wizard UI.

Answers, at authoring time, "if I pick these instruments for a local project,
will it be able to run?" — which is worth far more than discovering it when the
measurement fails at 2am. Everything here is static: the declarations come from
the params classes, and the live part is one cheap query to the local server.

Two distinct warnings, and the difference matters:

* **held** — the server has this rack open *now*. A local project will fail.
* **configured** — the server does not hold it yet, but serves it. A local
  project works today and breaks the moment anything touches that rack through
  the server. Surfacing only ``held`` would let someone author a project that
  is quietly a time bomb.

Shared transports never appear: a rack behind its own multiplexing server is
usable concurrently by design.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from lab_wizard.lib.server.registry import InstrumentRegistry, root_path
from lab_wizard.wizard.backend.server_control import server_status


logger = logging.getLogger("lab_wizard.wizard.backend.transport_status")

__all__ = ["transport_overview", "conflicts_for_selection"]


def _loopback(bind: Optional[str]) -> Optional[str]:
    if not bind:
        return None
    return bind.replace("://0.0.0.0:", "://127.0.0.1:").replace("://*:", "://127.0.0.1:")


def _held_from(url: str, timeout_ms: int = 1500) -> dict[str, Any]:
    """Ask one server what it holds. Unreachable is not an error."""
    from lab_wizard.lib.client.session import Session

    try:
        session = Session(url, timeout_ms=timeout_ms)
        try:
            return session.call("list_held") or {}
        finally:
            session.close()
    except Exception as exc:  # noqa: BLE001 - a stopped server is normal
        logger.debug("Server at %s did not answer list_held: %s", url, exc)
        return {}


def _machine_held(timeout_ms: int = 1500) -> dict[str, Any]:
    """Union of what *every* server on this machine holds.

    Not just this workspace's. A server started elsewhere on the box holds real
    hardware, and its endpoint is not derivable from here — so the authoring
    check has to enumerate the machine-local registry or it will cheerfully
    approve a project that cannot run.

    Held transport keys are collected alongside roots because the same device
    reached through another workspace's config has a different root hash.
    """
    from lab_wizard.lib.client.server_registry import (
        list_local_servers,
        local_server_endpoints,
    )

    held_roots: set[str] = set()
    held_keys: dict[str, dict[str, Any]] = {}
    servers: list[dict[str, Any]] = []

    for entry in list_local_servers():
        endpoints = local_server_endpoints(entry)
        if not endpoints:
            continue
        held = _held_from(endpoints[0], timeout_ms)
        if not held:
            continue
        servers.append(
            {
                "url": endpoints[0],
                "workspace_path": entry.get("workspace_path"),
                "config_dir": entry.get("config_dir"),
                "pid": entry.get("pid"),
                "held_roots": held.get("held_roots") or [],
            }
        )
        held_roots.update(held.get("held_roots") or [])
        for root, info in (held.get("exclusive_roots") or {}).items():
            key = (info or {}).get("transport_key")
            if key and root in set(held.get("held_roots") or []):
                held_keys[key] = {"root": root, "url": endpoints[0]}

    return {"servers": servers, "held_roots": held_roots, "held_keys": held_keys}


def transport_overview(config_dir: str | Path) -> dict[str, Any]:
    """Per-root transport facts for this workspace, plus live server state.

    Returned to the UI so a tree node can show whether it is exclusive or
    shared and whether the server currently holds it.
    """
    registry = InstrumentRegistry.from_config_dir(str(config_dir))
    status = server_status(config_dir)
    machine = _machine_held()
    held_roots = machine["held_roots"]
    held_keys = machine["held_keys"]

    roots: dict[str, Any] = {}
    for path in registry.list_paths():
        root = root_path(path)
        if root in roots:
            continue
        info = registry.transport_for(root)
        key = info["transport_key"]
        # Held here, or held elsewhere on the machine via the same transport.
        elsewhere = held_keys.get(key) if key else None
        roots[root] = {
            "root": root,
            "transport_sharing": info["transport_sharing"],
            "state_authority": info["state_authority"],
            "transport_key": key,
            "held_by_server": root in held_roots or elsewhere is not None,
            "held_by": (elsewhere or {}).get("url"),
        }

    # Two roots resolving to one transport key are the same physical device
    # configured twice — a real and easily-made config mistake.
    by_key: dict[str, list[str]] = {}
    for root, info in roots.items():
        key = info["transport_key"]
        if key:
            by_key.setdefault(key, []).append(root)
    duplicates = {k: sorted(v) for k, v in by_key.items() if len(v) > 1}

    return {
        "server_running": bool(status.get("running")),
        # Every server on the machine, so the UI can say *which* workspace holds
        # a rack rather than just that something does.
        "local_servers": machine["servers"],
        "roots": roots,
        "duplicate_transports": duplicates,
    }


def conflicts_for_selection(
    config_dir: str | Path, selected_paths: list[str]
) -> dict[str, Any]:
    """Would a *local* project using ``selected_paths`` contend with the server?

    ``selected_paths`` are ``inst://`` paths (or attribute-resolved paths) the
    measurement will use. Only exclusive roots can conflict.
    """
    overview = transport_overview(config_dir)
    roots = overview["roots"]

    held: list[dict[str, Any]] = []
    configured: list[dict[str, Any]] = []
    for root in sorted({root_path(p) for p in selected_paths}):
        info = roots.get(root)
        if info is None or info["transport_sharing"] == "shared":
            continue
        (held if info["held_by_server"] else configured).append(info)

    any_server = bool(overview["local_servers"])
    return {
        "server_running": overview["server_running"],
        "local_servers": overview["local_servers"],
        # Local run fails now.
        "held_conflicts": held,
        # Local run works now, breaks as soon as a server touches this rack.
        "configured_conflicts": configured if any_server else [],
        "duplicate_transports": overview["duplicate_transports"],
    }
