"""Start/stop the local instrument server from the wizard UI.

The wizard can launch ``python -m lab_wizard.lib.server.server`` against this
workstation's ``config/server/server.yaml`` in one of two lifecycle modes:

- **managed** (default): the server runs as a child of the wizard; closing the
  wizard stops it (see :func:`stop_managed_children`, wired into the wizard's
  shutdown).
- **detached**: the server is started in its own session (``setsid``) so it
  keeps running like a daemon after the wizard exits. The wizard re-attaches to
  it across restarts via the pid file.

A single pid file (``config/server/.server.pid``) records the running server's
pid, bind address, and mode, so status survives a wizard restart. Only one
server per workstation is expected (one bind address).
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger("lab_wizard.wizard.backend.server_control")

# Handles to servers we launched as children (managed mode), so the wizard can
# terminate them on shutdown. Detached servers are intentionally not tracked.
_managed_children: list[subprocess.Popen] = []


def _server_yaml_path(config_dir: str | Path) -> Path:
    return Path(config_dir) / "server" / "server.yaml"


def _pid_file(config_dir: str | Path) -> Path:
    return Path(config_dir) / "server" / ".server.pid"


def _log_file(config_dir: str | Path) -> Path:
    return Path(config_dir) / "server" / "server.log"


def _read_server_yaml(config_dir: str | Path) -> dict[str, Any]:
    path = _server_yaml_path(config_dir)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _configured_bind(config_dir: str | Path) -> Optional[str]:
    return (_read_server_yaml(config_dir).get("server") or {}).get("bind")


def _rule_count(config_dir: str | Path) -> int:
    perms = _read_server_yaml(config_dir).get("permissions") or {}
    rules = perms.get("rules") or []
    return len(rules) if isinstance(rules, list) else 0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid_file(config_dir: str | Path) -> Optional[dict[str, Any]]:
    path = _pid_file(config_dir)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_pid_file(config_dir: str | Path) -> None:
    try:
        _pid_file(config_dir).unlink()
    except OSError:
        pass


def _log_tail(config_dir: str | Path, n: int = 20) -> str:
    path = _log_file(config_dir)
    if not path.exists():
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-n:])
    except OSError:
        return ""


def server_status(config_dir: str | Path) -> dict[str, Any]:
    """Return the current server status, reconciling the pid file with reality.

    A stale pid file (process no longer alive) is cleaned up here so status is
    always truthful.
    """
    info = _read_pid_file(config_dir)
    running = False
    pid = None
    detached = False
    if info and isinstance(info.get("pid"), int) and _pid_alive(info["pid"]):
        running = True
        pid = info["pid"]
        detached = bool(info.get("detached"))
    elif info:
        # Recorded but dead -> stale; clean it.
        _clear_pid_file(config_dir)

    return {
        "running": running,
        "pid": pid,
        "detached": detached,
        "bind": _configured_bind(config_dir),
        "rule_count": _rule_count(config_dir),
        "has_config": _server_yaml_path(config_dir).exists(),
    }


def start_server(config_dir: str | Path, detached: bool = False) -> dict[str, Any]:
    """Launch the instrument server. No-op (returns status) if already running.

    Raises ``ValueError`` if there is no ``server.yaml`` to launch against, or
    if the process dies immediately (the recent log tail is included).
    """
    status = server_status(config_dir)
    if status["running"]:
        return status

    config_path = _server_yaml_path(config_dir)
    if not config_path.exists():
        raise ValueError(
            f"No server config at {config_path}. Configure permissions/bind first."
        )

    cmd = [
        sys.executable,
        "-m",
        "lab_wizard.lib.server.server",
        "--config",
        str(config_path),
    ]

    log_path = _log_file(config_dir)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fh = open(log_path, "w", encoding="utf-8")

    # ``start_new_session`` detaches the child into its own process group/session
    # so it is not killed when the wizard (its parent) exits.
    popen_kwargs: dict[str, Any] = {
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
    }
    if detached:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)

    # Give it a moment; if it exits immediately something is wrong (bad bind,
    # config error) and we surface the log rather than reporting a phantom run.
    time.sleep(0.6)
    if proc.poll() is not None:
        log_fh.close()
        tail = _log_tail(config_dir)
        raise ValueError(
            f"Server exited immediately (code {proc.returncode}).\n{tail}".strip()
        )

    if not detached:
        _managed_children.append(proc)

    with open(_pid_file(config_dir), "w", encoding="utf-8") as f:
        json.dump(
            {
                "pid": proc.pid,
                "bind": _configured_bind(config_dir),
                "detached": detached,
            },
            f,
        )

    logger.info(
        "Started instrument server pid=%s detached=%s bind=%s",
        proc.pid,
        detached,
        _configured_bind(config_dir),
    )
    return server_status(config_dir)


def stop_server(config_dir: str | Path, timeout_s: float = 5.0) -> dict[str, Any]:
    """Stop the running server (SIGTERM, then SIGKILL), regardless of mode."""
    info = _read_pid_file(config_dir)
    if not info or not isinstance(info.get("pid"), int):
        return server_status(config_dir)

    pid = info["pid"]
    if _pid_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        deadline = time.time() + timeout_s
        while time.time() < deadline and _pid_alive(pid):
            time.sleep(0.1)
        if _pid_alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # Drop (and reap, if managed) any handle for this pid.
    for proc in [p for p in _managed_children if p.pid == pid]:
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
    _managed_children[:] = [p for p in _managed_children if p.pid != pid]
    _clear_pid_file(config_dir)
    logger.info("Stopped instrument server pid=%s", pid)
    return server_status(config_dir)


def restart_server(config_dir: str | Path, detached: bool = False) -> dict[str, Any]:
    """Stop (if running) then start — used to apply edited permission rules."""
    stop_server(config_dir)
    return start_server(config_dir, detached=detached)


def stop_managed_children() -> None:
    """Terminate every server we launched in managed mode.

    Wired into the wizard's shutdown so closing the wizard stops servers started
    *without* the detached option. Detached servers are left running.
    """
    for proc in _managed_children:
        if proc.poll() is None:
            try:
                proc.terminate()
                # Reap so the child does not linger as a zombie (a zombie still
                # answers os.kill(pid, 0), which would look "alive" to status).
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            except OSError:
                pass
    _managed_children.clear()
