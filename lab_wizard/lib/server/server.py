"""CLI entry point for the lab_wizard server.

Usage:
    python -m lab_wizard.lib.server.server --config path/to/server.yaml

The config file is a small YAML:

    server:
      bind: tcp://0.0.0.0:12300
      # Optional. Directory containing an `instruments/` tree. Defaults to the
      # parent of this file's directory (i.e. the workspace config/). The server
      # hosts every configured instrument with an attribute_name.
      config_dir: ..
      # Optional override: host a single project's resources instead of config_dir.
      # project_yaml: ../../../projects/foo/foo.yaml
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from typing import Any

import yaml

from lab_wizard.lib.client.server_discovery import workspace_ipc_endpoint
from lab_wizard.lib.client.server_registry import advertise_server, withdraw_server
from lab_wizard.lib.server.external_state import attach_external_state
from lab_wizard.lib.server.permissions import PermissionGate, load_permissions
from lab_wizard.lib.server.registry import InstrumentRegistry
from lab_wizard.lib.server.wire import WireServer
from lab_wizard.lib.utilities.model_tree import load_project_config


def _load_server_config(path: Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict) or "server" not in cfg:
        raise ValueError(
            f"Server config at {path} must be a YAML mapping with a 'server' key"
        )
    server = cfg["server"]
    if "bind" not in server:
        raise ValueError("server config must contain a 'bind' key")
    return cfg


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="lab_wizard.lib.server.server",
        description="Run the lab_wizard remote instrument server (Phase 1).",
    )
    parser.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to a workspace server YAML config (normally config/server/server.yaml).",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("lab_wizard.server")

    config_path: Path = args.config.expanduser().resolve()
    cfg = _load_server_config(config_path)

    bind: str = cfg["server"]["bind"]
    server_cfg = cfg["server"]

    # Resolved up front (not only in the default branch) because the ipc
    # endpoint is derived from it, and a client computes the same path from its
    # own workspace — the two must agree in every hosting mode.
    config_dir = (
        config_path.parent / server_cfg["config_dir"]
        if server_cfg.get("config_dir")
        else config_path.parent.parent
    ).resolve()

    if server_cfg.get("project_yaml"):
        # Override mode: host a single project's resources (eager — opens hardware).
        project_yaml = (config_path.parent / server_cfg["project_yaml"]).resolve()
        log.info("Loading project from %s (override mode)", project_yaml)
        project = load_project_config(project_yaml)
        log.info("Instantiating instrument tree (this opens hardware connections)")
        registry = InstrumentRegistry(project.resources)
    else:
        # Default mode: host the whole config/instruments tree (lazy — hardware
        # opens on first request).
        log.info("Hosting config/instruments tree from %s (lazy)", config_dir)
        registry = InstrumentRegistry.from_config_dir(str(config_dir))

    paths = registry.list_paths()
    log.info("Registered %d paths:", len(paths))
    for p in paths:
        # describe_path uses static metadata — does not open hardware.
        log.info("  %s -> %s", p, registry.describe_path(p).get("type_hint"))
    attrs = registry.list_attributes()
    if attrs:
        log.info("Named attributes:")
        for name, path in attrs.items():
            log.info("  %s -> %s", name, path)

    perms = load_permissions(cfg.get("permissions"))
    gate = PermissionGate(perms, attribute_resolver=registry.resolve_attribute_path)
    if perms.rules:
        log.info("Loaded %d permission rule(s):", len(perms.rules))
        for rule in perms.rules:
            log.info("  %s — %s", rule.id, rule.description or "(no description)")
    else:
        log.info("No permission rules configured (all calls allowed)")

    # Roots whose state another process owns are subscribed rather than
    # inferred, so a change made outside lab_wizard (someone using the DBay GUI)
    # is reflected in the gate instead of leaving it confidently stale.
    bridges = attach_external_state(registry, gate.tracker)
    if bridges:
        log.info("Subscribed to %d external state authority(ies)", len(bridges))

    # Same-machine clients (the wizard, locally-run projects) reach the server
    # over an ipc:// socket derived from the config dir, so they need no
    # discovery and are unaffected by the tcp bind being reconfigured. The
    # tcp:// bind stays for remote clients. One socket, two endpoints.
    binds = [bind]
    ipc_endpoint = workspace_ipc_endpoint(config_dir)
    if server_cfg.get("ipc", True):
        binds.append(ipc_endpoint)

    server = WireServer(
        bind=binds,
        registry=registry,
        gate=gate,
        # Only config-dir hosting has a servable tree; project_yaml mode does not.
        config_dir=None if server_cfg.get("project_yaml") else str(config_dir),
    )

    def _handle_signal(signum: int, _frame: Any) -> None:
        log.info("Received signal %s; shutting down", signum)
        server.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # Publish to the machine-local registry so a workspace that knows nothing
    # about this one can still find us. Its ipc path is hashed from *our* config
    # dir, so it is not derivable from elsewhere.
    advertise_server(
        config_dir,
        bind=bind,
        ipc=ipc_endpoint if server_cfg.get("ipc", True) else None,
    )
    try:
        log.info("WireServer ready on %s — Ctrl-C to exit", ", ".join(binds))
        server.serve_forever()
    finally:
        for bridge in bridges:
            bridge.stop()
        withdraw_server(config_dir)
    log.info("WireServer stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
