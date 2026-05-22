"""Backend for the Remote Servers page (the *consuming* side).

A workstation can register remote lab_wizard servers it wants to *use* in its
measurements. This module owns ``config/remote/servers.yaml``:

    servers:
      - name: cryo-rack
        url: tcp://10.0.0.5:12300

and provides:
  * CRUD over that file,
  * a live connection test (``RemoteExp.connect`` + ``list_descriptions``),
  * aggregation of every reachable server's named attributes, each tagged with
    its ``behavior_abc`` so measurement creation can match remote attributes to
    a measurement's required resource types exactly like local instruments.

This is independent of the permission gate: ``servers.yaml`` is purely a client
address book and never feeds server-side rules.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML as RuamelYAML

from lab_wizard.lib.client.remote_exp import RemoteExp

logger = logging.getLogger("lab_wizard.wizard.backend.remote_servers")


def _servers_yaml_path(config_dir: str | Path) -> Path:
    return Path(config_dir) / "remote" / "servers.yaml"


def load_remote_servers(config_dir: str | Path) -> list[dict[str, str]]:
    """Return the registered servers as ``[{name, url}]`` (empty if none)."""
    path = _servers_yaml_path(config_dir)
    if not path.exists():
        return []
    yaml = RuamelYAML(typ="rt")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.load(f) or {}
    servers = data.get("servers") or []
    out: list[dict[str, str]] = []
    for entry in servers:
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        if name and url:
            out.append({"name": name, "url": url})
    return out


def save_remote_servers(
    config_dir: str | Path, servers: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Persist the full servers list, validating shape and uniqueness."""
    cleaned: list[dict[str, str]] = []
    seen_names: set[str] = set()
    for entry in servers:
        name = str(entry.get("name", "")).strip()
        url = str(entry.get("url", "")).strip()
        if not name or not url:
            raise ValueError("Each remote server needs a non-empty 'name' and 'url'")
        if name in seen_names:
            raise ValueError(f"Duplicate remote server name: {name!r}")
        seen_names.add(name)
        cleaned.append({"name": name, "url": url})

    path = _servers_yaml_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = RuamelYAML(typ="rt")
    yaml.default_flow_style = False
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"servers": cleaned}, f)
    return cleaned


def add_remote_server(
    config_dir: str | Path, name: str, url: str
) -> list[dict[str, str]]:
    """Add (or update by name) a remote server and persist."""
    servers = load_remote_servers(config_dir)
    servers = [s for s in servers if s["name"] != name]
    servers.append({"name": name, "url": url})
    return save_remote_servers(config_dir, servers)


def remove_remote_server(config_dir: str | Path, name: str) -> list[dict[str, str]]:
    """Remove a remote server by name and persist."""
    servers = [s for s in load_remote_servers(config_dir) if s["name"] != name]
    return save_remote_servers(config_dir, servers)


def test_connection(url: str, *, timeout_ms: int = 3000) -> dict[str, Any]:
    """Try to connect to ``url`` and enumerate its attributes.

    Returns ``{ok, attributes?, error?}`` — never raises, so the UI can show a
    friendly status for an unreachable server.
    """
    try:
        exp = RemoteExp.connect(url, timeout_ms=timeout_ms)
        try:
            descriptions = exp.list_descriptions()
        finally:
            exp.close()
        return {"ok": True, "attributes": descriptions}
    except Exception as e:  # noqa: BLE001 - surfaced to the UI as a status
        logger.warning("Remote server test failed for %s: %s", url, e)
        return {"ok": False, "error": str(e)}


def list_remote_attributes(
    config_dir: str | Path, *, timeout_ms: int = 3000
) -> list[dict[str, Any]]:
    """Aggregate named attributes across all reachable registered servers.

    Each entry: ``{server_name, url, attribute, behavior_abc, type_hint}``.
    Unreachable servers are skipped (best-effort); their failure is logged.
    """
    out: list[dict[str, Any]] = []
    for server in load_remote_servers(config_dir):
        result = test_connection(server["url"], timeout_ms=timeout_ms)
        if not result.get("ok"):
            continue
        for desc in result.get("attributes", []) or []:
            out.append(
                {
                    "server_name": server["name"],
                    "url": server["url"],
                    "attribute": desc.get("attribute_name"),
                    "behavior_abc": desc.get("behavior_abc"),
                    "type_hint": desc.get("type_hint"),
                }
            )
    return out


def remote_matches_for_base_type(
    config_dir: str | Path, base_type_name: str, *, timeout_ms: int = 3000
) -> list[dict[str, Any]]:
    """Remote attributes whose ``behavior_abc`` matches ``base_type_name``.

    ``base_type_name`` is the simple class name of the measurement's required
    resource type (e.g. ``"VSource"``). Matching is by behavior-ABC name, the
    same contract local discovery uses.
    """
    return [
        attr
        for attr in list_remote_attributes(config_dir, timeout_ms=timeout_ms)
        if attr.get("behavior_abc") == base_type_name
    ]
