"""Run a project from the wizard: start it, follow it, stop it.

A run is the project's own setup file, started exactly as a person would start
it from a terminal, as a process of its own. So it runs the same code, claims
the same instruments and records the same way; and because it is detached, a
wizard that crashes or restarts mid-sweep does not stop it. The wizard finds
it again from what is written in ``<project>/.wizard/``:

``launch.json``      the process, when it started, and where its output goes
``<id>.run.json``    written by the run itself (``LAUNCH_FILE_ENV``) once it
                     has a run id in the lab database — until then it is
                     still claiming and resolving its instruments

Stop sends SIGINT, which is Ctrl-C: the run aborts through its own guards and
``RunLifecycle`` puts the instruments in their safe state.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lab_wizard.lib.task_adapters.run import LAUNCH_FILE_ENV

__all__ = ["LaunchError", "launch", "launch_status", "stop"]

# Processes this wizard started, so their exit is collected (not left a zombie).
_children: dict[int, subprocess.Popen[bytes]] = {}


class LaunchError(RuntimeError):
    """A project cannot be started, or stopped, now."""


def _state_dir(project_dir: Path) -> Path:
    return project_dir / ".wizard"


def _read_state(project_dir: Path) -> dict[str, Any] | None:
    path = _state_dir(project_dir) / "launch.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _alive(pid: int) -> tuple[bool, int | None]:
    """Whether ``pid`` is still running, and its exit code if we know it."""
    child = _children.get(pid)
    if child is not None:
        code = child.poll()
        return code is None, code
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False, None
    except PermissionError:
        return True, None
    return True, None


def _tail(path: Path, lines: int = 60) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


def launch_status(project_dir: Path) -> dict[str, Any]:
    """``idle`` (never run from here), ``starting``, ``running`` or ``ended``, and details."""
    state = _read_state(project_dir)
    if state is None:
        return {"state": "idle"}
    alive, code = _alive(state["pid"])
    run: dict[str, Any] = {}
    try:
        run = json.loads(Path(state["launch_file"]).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    if alive:
        phase = "running" if run.get("run_id") else "starting"
    else:
        phase = "ended"
    return {
        "state": phase,
        "launch_id": state["launch_id"],
        "pid": state["pid"],
        "started_at": state["started_at"],
        "run_id": run.get("run_id"),
        "exit_code": code if not alive else None,
        "log": _tail(Path(state["log"])),
        "log_file": state["log"],
    }


def launch(project_dir: Path) -> dict[str, Any]:
    """Start the project's setup file; ``LaunchError`` if it is already running."""
    current = launch_status(project_dir)
    if current["state"] in ("starting", "running"):
        raise LaunchError(f"{project_dir.name} is already running (process {current['pid']})")
    setups = sorted(project_dir.glob("*_setup.py"))
    if not setups:
        raise LaunchError(f"{project_dir.name} has no setup file to run")

    state_dir = _state_dir(project_dir)
    state_dir.mkdir(exist_ok=True)
    launch_id = uuid.uuid4().hex[:12]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logs = project_dir / "logs"
    logs.mkdir(exist_ok=True)
    log = logs / f"run_{stamp}.log"
    launch_file = state_dir / f"{launch_id}.run.json"

    env = {**os.environ, LAUNCH_FILE_ENV: str(launch_file), "PYTHONUNBUFFERED": "1"}
    with log.open("wb") as out:
        child = subprocess.Popen(
            [sys.executable, str(setups[0])],
            cwd=str(project_dir),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # outlives the wizard, and is stopped as a group
        )
    _children[child.pid] = child
    state = {
        "launch_id": launch_id,
        "pid": child.pid,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log": str(log),
        "launch_file": str(launch_file),
    }
    (state_dir / "launch.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    return launch_status(project_dir)


def stop(project_dir: Path) -> dict[str, Any]:
    """Ctrl-C the running project: it aborts and makes its instruments safe."""
    current = launch_status(project_dir)
    if current["state"] not in ("starting", "running"):
        raise LaunchError(f"{project_dir.name} is not running")
    try:
        os.killpg(current["pid"], signal.SIGINT)
    except ProcessLookupError:
        pass
    return launch_status(project_dir)
