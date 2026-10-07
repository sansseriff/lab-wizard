"""``wizard sim``: the simulated bench, served by a process of its own.

The bench runs apart from the terminal that started it and from any wizard
session, because measurements run from generated projects and need it up
whenever they do. It belongs to the machine, not to a workspace (its ports and
its Prologix link are fixed), so its state lives beside that link, in
``~/.lab_sim``:

- ``bench.json``, written by a bench once it is serving: its pid, the bench
  file it was started from, and the table of addresses it prints.
- ``bench.log``, the background bench's output.

:func:`serve` is the bench process itself (``python -m lab_wizard.sim``, which
also runs it in the foreground). :func:`start` and :func:`stop` are what
``wizard sim`` and ``wizard sim stop`` call.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from lab_wizard.sim.bench import Bench, BenchConfig

# Overridable so the tests can run a background bench without touching the
# real one.
HOME = Path(os.environ.get("LAB_WIZARD_SIM_HOME", "~/.lab_sim")).expanduser()
STATE = HOME / "bench.json"
LOG = HOME / "bench.log"
START_TIMEOUT_S = 15.0
STOP_TIMEOUT_S = 5.0


class SimError(Exception):
    """``wizard sim`` could not do what it was asked; the message says why."""


@dataclass(frozen=True)
class Running:
    pid: int
    config: str | None
    description: str


def running() -> Running | None:
    """The bench serving on this machine, if there is one.

    A state file left by a bench that died without cleaning up is removed.
    """
    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return None
    pid = data.get("pid")
    if not isinstance(pid, int) or not _is_bench(pid):
        STATE.unlink(missing_ok=True)
        return None
    return Running(pid, data.get("config"), data.get("description", ""))


def start(config: Path | None = None, verbose: bool = False) -> Running:
    """Start a bench in the background and return it once it is serving.

    If one is already running from the same bench file, that one is returned.
    """
    config = config.expanduser().resolve() if config else None
    if config is not None and not config.is_file():
        raise SimError(f"no bench file at {config}")
    wanted = str(config) if config else None

    current = running()
    if current is not None:
        if current.config != wanted:
            raise SimError(
                f"a bench is already running from {current.config or 'the standard bench'} "
                f"(pid {current.pid}); stop it first with `wizard sim stop`"
            )
        return current

    HOME.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "lab_wizard.sim"]
    if config is not None:
        command.append(str(config))
    if verbose:
        command.append("--verbose")
    with LOG.open("w", encoding="utf-8") as log:
        # Its own session, so closing this terminal or pressing Ctrl-C in it
        # leaves the bench running.
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )

    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        current = running()
        if current is not None and current.pid == process.pid:
            return current
        if process.poll() is not None:
            raise SimError(f"the bench did not start: {_last_line(LOG)}\n(full output in {LOG})")
        time.sleep(0.05)
    process.terminate()
    raise SimError(f"the bench did not start within {START_TIMEOUT_S:.0f} s (output in {LOG})")


def stop() -> int | None:
    """Stop the running bench and return its pid, or None if none was running."""
    current = running()
    if current is None:
        return None
    os.kill(current.pid, signal.SIGTERM)
    if not _wait_gone(current.pid, STOP_TIMEOUT_S):
        os.kill(current.pid, signal.SIGKILL)
        _wait_gone(current.pid, STOP_TIMEOUT_S)
    STATE.unlink(missing_ok=True)
    return current.pid


def serve(argv: list[str] | None = None) -> None:
    """Serve the bench until SIGTERM or Ctrl-C: the bench process itself."""
    parser = argparse.ArgumentParser(
        prog="python -m lab_wizard.sim",
        description="Serve the simulated bench in the foreground. `wizard sim` runs this in the background.",
    )
    parser.add_argument("config", nargs="?", type=Path, help="bench YAML (detector constants, ports); defaults if omitted")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every unrecognised command")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(name)s: %(message)s")

    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    with Bench(BenchConfig.load(args.config)) as bench:
        description = bench.describe()
        print(description, flush=True)
        _write_state(str(args.config.resolve()) if args.config else None, description)
        try:
            stopping.wait()
        finally:
            _clear_state()


def _write_state(config: str | None, description: str) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    partial = STATE.with_suffix(".partial")
    partial.write_text(json.dumps({"pid": os.getpid(), "config": config, "description": description}), encoding="utf-8")
    partial.replace(STATE)  # never a half-written file for start() to read


def _clear_state() -> None:
    """Remove the state file, if it is still this process's."""
    try:
        if json.loads(STATE.read_text(encoding="utf-8")).get("pid") == os.getpid():
            STATE.unlink()
    except (FileNotFoundError, ValueError):
        pass


def _is_bench(pid: int) -> bool:
    """Whether *pid* is a bench, not some later process that reused its pid."""
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    command = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True).stdout
    return "lab_wizard.sim" in command


def _wait_gone(pid: int, timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while _is_bench(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def _last_line(path: Path) -> str:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return lines[-1] if lines else "it exited without output"
