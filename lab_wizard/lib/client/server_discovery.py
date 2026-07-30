"""Find the instrument server belonging to a workspace.

A generated project needs to know whether *this workstation* runs a server, so
it can check for hardware conflicts before opening anything. The answer is
already on disk — ``config/server/server.yaml`` records the bind address — so
this is a file read, never a port scan. Scanning would be slow, ambiguous, and
liable to point a project at the wrong rack.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
from pathlib import Path
from typing import Optional

import yaml


logger = logging.getLogger(__name__)

__all__ = ["local_server_url", "find_workspace_config_dir"]


def find_workspace_config_dir(start: Path | str) -> Optional[Path]:
    """Walk upward from ``start`` for a workspace ``config/`` directory.

    Generated projects live in ``<workspace>/projects/<name>/``, so the config
    tree is a couple of levels up; searching upward keeps that from being
    hard-coded and survives a project folder being moved deeper.
    """
    current = Path(start).expanduser().resolve()
    for candidate in [current, *current.parents]:
        config_dir = candidate / "config"
        if (config_dir / "server").is_dir() or (config_dir / "instruments").is_dir():
            return config_dir
    return None


def local_server_url(start: Path | str) -> Optional[str]:
    """Bind address of this workspace's server, or ``None`` if not configured.

    Returning a URL says only that a server is *configured* — not that one is
    running. Callers treat an unreachable address as "no server".
    """
    config_dir = find_workspace_config_dir(start)
    if config_dir is None:
        return None

    server_yaml = config_dir / "server" / "server.yaml"
    if not server_yaml.exists():
        return None

    try:
        with open(server_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("Could not read %s: %s", server_yaml, exc)
        return None

    bind = (data.get("server") or {}).get("bind")
    if not isinstance(bind, str) or not bind:
        return None

    # A server binds 0.0.0.0 to accept remote clients; we are on the same host,
    # so dial loopback rather than trying to connect to a wildcard address.
    return bind.replace("://0.0.0.0:", "://127.0.0.1:").replace("://*:", "://127.0.0.1:")


# --------------------------- same-machine endpoint ---------------------------


def workspace_ipc_endpoint(config_dir: Path | str) -> str:
    """Deterministic ``ipc://`` endpoint for a workspace's server.

    Derived from the resolved config path, so the server and any process on the
    same machine compute the same answer without discovery. Hashed rather than
    embedding the path because socket paths are length-limited (~104 bytes on
    macOS) and a deep workspace path would overflow it.

    Note this is per *workspace*: two workspaces on one machine get different
    sockets and do not collide. It also means a GUI opened in workspace B
    cannot derive workspace A's endpoint — cross-workspace discovery needs the
    machine-local registry (plan 4.4), not this.
    """
    resolved = str(Path(config_dir).expanduser().resolve())
    digest = hashlib.sha256(resolved.encode()).hexdigest()[:12]
    return f"ipc://{tempfile.gettempdir()}/lab_wizard-{digest}.sock"


def local_endpoints(config_dir: Path | str) -> list[str]:
    """Endpoints a client on this machine should try, best first.

    ipc:// is preferred: lower latency than a TCP loopback and unaffected by
    the bind address being reconfigured.
    """
    endpoints = [workspace_ipc_endpoint(config_dir)]
    url = local_server_url(config_dir)
    if url:
        endpoints.append(url)
    return endpoints


# --------------------------- address book ---------------------------


def load_server_urls(start: Path | str) -> dict[str, str]:
    """``{server_name: url}`` from this workspace's ``config/remote/servers.yaml``.

    The address book is a *client* concern: it records servers this workspace
    wants to consume, which is exactly what a project's ``instrument_sources``
    entries name. Kept here so a generated project can resolve those names
    without reaching into wizard backend code.
    """
    config_dir = find_workspace_config_dir(start)
    if config_dir is None:
        return {}

    servers_yaml = config_dir / "remote" / "servers.yaml"
    if not servers_yaml.exists():
        return {}

    try:
        with open(servers_yaml, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.debug("Could not read %s: %s", servers_yaml, exc)
        return {}

    out: dict[str, str] = {}
    for entry in data.get("servers") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        if name and url:
            out[name] = url
    return out
