"""Command-line entry point for Lab Wizard workspaces and the UI."""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys

from lab_wizard.lib.workspace import (
    WORKSPACE_ENV,
    clean_workspace,
    initialize_workspace,
    load_workspace,
    require_workspace,
)


def _run_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wizard",
        description="Launch the Lab Wizard UI",
        epilog="Workspace commands: wizard init [PATH], wizard clean [PATH]. Project commands: wizard regenerate [PROJECT]",
    )
    parser.add_argument(
        "--workspace",
        help="Workspace root or lab-wizard.toml (default: search current directory and parents)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=(
            "Port for the wizard UI. Omit to prefer 8884 and fall back to a "
            "free port, so a second workspace opens its own wizard rather than "
            "colliding with the first."
        ),
    )
    parser.add_argument("--no-ui", action="store_true")
    parser.add_argument("--debug", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)

    if argv and argv[0] == "init":
        parser = argparse.ArgumentParser(prog="wizard init")
        parser.add_argument("path", nargs="?", default=".")
        args = parser.parse_args(argv[1:])
        workspace, created = initialize_workspace(args.path)
        action = "Created" if created else "Initialized existing"
        print(f"{action} Lab Wizard workspace at {workspace.root}")
        return

    if argv and argv[0] == "regenerate":
        parser = argparse.ArgumentParser(
            prog="wizard regenerate",
            description=(
                "Bring a project's Python up to date with its YAML: build <name>_measurement.py from "
                "its procedure: block, and write its setup's roles from its roles: block."
            ),
        )
        parser.add_argument("project", nargs="?", default=".", help="the project folder (default: here)")
        args = parser.parse_args(argv[1:])
        from lab_wizard.wizard.backend.regenerate import RegenerateError, regenerate_project

        try:
            done = regenerate_project(args.project)
        except (RegenerateError, ValueError) as exc:
            parser.exit(1, f"wizard regenerate: {exc}\n")
        print("\n".join(done) if done else "Already up to date.")
        return

    if argv and argv[0] == "clean":
        parser = argparse.ArgumentParser(prog="wizard clean")
        parser.add_argument("path", nargs="?")
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Delete workspace state without an interactive confirmation",
        )
        args = parser.parse_args(argv[1:])
        try:
            workspace = (
                load_workspace(args.path)
                if args.path
                else require_workspace(use_environment=False)
            )
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))

        print(f"Workspace: {workspace.root}")
        print(f"  remove {workspace.config_dir}")
        print(f"  remove {workspace.projects_dir}")
        print(f"  remove {workspace.logs_dir}")
        print(f"  remove {workspace.manifest}")
        print(f"  keep   {workspace.data_dir} (recorded measurements are never cleaned)")
        if not args.yes and input(
            "Return this workspace to its uninitialized state? [y/N] "
        ).lower() not in {
            "y",
            "yes",
        }:
            print("Clean cancelled.")
            return

        try:
            clean_workspace(workspace)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Cleaned Lab Wizard workspace at {workspace.root}")
        return

    parser = _run_parser()
    args = parser.parse_args(argv)
    try:
        workspace = (
            load_workspace(args.workspace) if args.workspace else require_workspace()
        )
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))

    environment = os.environ.copy()
    environment[WORKSPACE_ENV] = str(workspace.root)
    command = [
        sys.executable,
        "-m",
        "lab_wizard.wizard.backend.main",
    ]
    # Only forward an explicit choice. Passing the default unconditionally made
    # every workspace demand the same port, so the second one could not start.
    if args.port is not None:
        command += ["--port", str(args.port)]
    if args.no_ui:
        command.append("--no-ui")
    if args.debug:
        command.append("--debug")
    raise SystemExit(_run_until_stopped(command, environment))


def _run_until_stopped(command: list[str], environment: dict[str, str]) -> int:
    """Run the wizard and return its exit code, letting *it* handle Ctrl-C.

    The terminal signals the whole process group, so the wizard already hears
    Ctrl-C and stops itself cleanly. ``subprocess.call`` would instead kill it
    a quarter-second into that shutdown, so here Ctrl-C is ignored while
    waiting.
    """
    process = subprocess.Popen(command, env=environment)
    # Set only after the child exists: an ignored signal is inherited across
    # exec, and the wizard must still receive it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    return process.wait()
