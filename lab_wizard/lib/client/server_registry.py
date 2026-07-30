"""Registry of instrument servers running on this machine.

``server_discovery`` answers "where is *my* workspace's server", which is not
enough. A workstation can host several workspaces, and a server started from one
of them holds real hardware that a project in another workspace may also want.
Its ipc endpoint is hashed from *its* config path, and its ``server.yaml`` lives
in a directory the other workspace has no reason to know about — so a
workspace-scoped lookup is structurally blind to it.

Each server therefore advertises itself in a well-known machine-local directory
on start and withdraws on stop:

    ~/.lab_wizard/servers/<workspace-hash>.json
    {bind, ipc, pid, workspace_path, config_dir, started_at}

Discovery is then a directory read — deterministic and instant. **Never a port
scan**: scanning is slow, gives ambiguous answers, and invites connecting to
the wrong rack.

A crashed server leaves its file behind, so entries are reconciled against
``pid`` liveness on every read, exactly as ``server_status`` already does for
its pid file. Filesystem visibility is also the authority model working in our
favour: an entry is only readable by someone who could read the config anyway.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional


logger = logging.getLogger(__name__)

__all__ = [
    "registry_dir",
    "advertise_server",
    "withdraw_server",
    "list_local_servers",
    "local_server_endpoints",
]


def registry_dir() -> Path:
    """Directory holding one descriptor per running server on this machine."""
    return Path(
        os.environ.get("LAB_WIZARD_SERVER_REGISTRY")
        or Path.home() / ".lab_wizard" / "servers"
    )


def _workspace_hash(config_dir: Path | str) -> str:
    resolved = str(Path(config_dir).expanduser().resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:12]


def _descriptor_path(config_dir: Path | str) -> Path:
    return registry_dir() / f"{_workspace_hash(config_dir)}.json"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def advertise_server(
    config_dir: Path | str,
    *,
    bind: Optional[str] = None,
    ipc: Optional[str] = None,
    pid: Optional[int] = None,
) -> Path:
    """Publish this server so any process on the machine can find it.

    Written atomically (temp file + replace) so a concurrent reader never sees a
    half-written descriptor.
    """
    config_dir = Path(config_dir).expanduser().resolve()
    path = _descriptor_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "bind": bind,
        "ipc": ipc,
        "pid": pid if pid is not None else os.getpid(),
        "workspace_path": str(config_dir.parent),
        "config_dir": str(config_dir),
        "started_at": time.time(),
    }
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    os.replace(tmp, path)
    logger.info("Advertised instrument server at %s", path)
    return path


def withdraw_server(config_dir: Path | str) -> None:
    """Remove this server's descriptor. Safe to call when absent."""
    try:
        _descriptor_path(config_dir).unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not withdraw server descriptor: %s", exc)


def list_local_servers(*, reap: bool = True) -> list[dict[str, Any]]:
    """Every live instrument server on this machine, newest first.

    Dead entries are dropped, and with ``reap`` also deleted — a crashed server
    would otherwise advertise forever and make callers dial a socket nobody is
    listening on.
    """
    directory = registry_dir()
    if not directory.is_dir():
        return []

    servers: list[dict[str, Any]] = []
    for path in directory.glob("*.json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                entry = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            logger.debug("Ignoring unreadable descriptor %s: %s", path, exc)
            continue

        pid = entry.get("pid")
        if not isinstance(pid, int) or not _pid_alive(pid):
            if reap:
                try:
                    path.unlink(missing_ok=True)
                    logger.debug("Reaped stale server descriptor %s", path)
                except OSError:
                    pass
            continue

        entry["descriptor"] = str(path)
        servers.append(entry)

    servers.sort(key=lambda e: e.get("started_at") or 0, reverse=True)
    return servers


def local_server_endpoints(entry: dict[str, Any]) -> list[str]:
    """Dialable endpoints for a descriptor, best first.

    ipc first (cheaper, and immune to the tcp bind being reconfigured). A
    wildcard bind is rewritten to loopback, since ``0.0.0.0`` is an address to
    listen on, not one to connect to.
    """
    endpoints: list[str] = []
    ipc = entry.get("ipc")
    if isinstance(ipc, str) and ipc:
        endpoints.append(ipc)
    bind = entry.get("bind")
    if isinstance(bind, str) and bind:
        endpoints.append(
            bind.replace("://0.0.0.0:", "://127.0.0.1:").replace("://*:", "://127.0.0.1:")
        )
    return endpoints
