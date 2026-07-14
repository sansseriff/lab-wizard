#!/usr/bin/env python3
"""Bump package versions and create release tags for PyPI publishing."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Package:
    name: str
    pyproject: Path
    tag_prefix: str


PACKAGES = {
    "lab-procedure": Package(
        name="lab-procedure",
        pyproject=ROOT / "procedure_framework" / "pyproject.toml",
        tag_prefix="lab-procedure-v",
    ),
    "lab-wizard": Package(
        name="lab-wizard",
        pyproject=ROOT / "lab_wizard" / "pyproject.toml",
        tag_prefix="lab-wizard-v",
    ),
}


def run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def project_version(pyproject: Path) -> str:
    in_project = False
    for line in pyproject.read_text().splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_project = False
        if in_project:
            match = re.fullmatch(r'version\s*=\s*"([^"]+)"', stripped)
            if match:
                return match.group(1)
    raise SystemExit(f"No [project] version field found in {pyproject}.")


def bump_version(version: str, part: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise SystemExit(f"Only simple MAJOR.MINOR.PATCH versions are supported, got {version!r}.")
    major, minor, patch = map(int, match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise AssertionError(part)


def set_project_version(pyproject: Path, new_version: str) -> None:
    lines = pyproject.read_text().splitlines(keepends=True)
    in_project = False
    changed = False
    output: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
        elif stripped.startswith("[") and stripped.endswith("]"):
            in_project = False

        if in_project and stripped.startswith("version = "):
            output.append(f'version = "{new_version}"\n')
            changed = True
        else:
            output.append(line)

    if not changed:
        raise SystemExit(f"No [project] version field found in {pyproject}.")
    pyproject.write_text("".join(output))


def assert_clean_worktree() -> None:
    status = run(["git", "status", "--porcelain"]).stdout.strip()
    if status:
        raise SystemExit(
            "Working tree is not clean. Commit or stash existing changes before releasing.\n\n"
            f"{status}"
        )


def ensure_tag_missing(tag: str) -> None:
    result = run(["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"], check=False)
    if result.returncode == 0:
        raise SystemExit(f"Tag {tag!r} already exists.")


def refresh_lock() -> None:
    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    if not Path(uv).exists() and shutil.which(uv) is None:
        print("Skipping uv lock: uv was not found on PATH or at ~/.local/bin/uv.", file=sys.stderr)
        return
    print("+ uv lock")
    subprocess.run([uv, "lock"], cwd=ROOT, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Bump a package version and optionally commit/tag/push the release.",
    )
    parser.add_argument("package", choices=PACKAGES)
    parser.add_argument("bump", nargs="?", choices=["patch", "minor", "major"])
    parser.add_argument("--version", help="Set an explicit version instead of bumping major/minor/patch.")
    parser.add_argument("--write", action="store_true", help="Write the version change and refresh uv.lock.")
    parser.add_argument("--commit", action="store_true", help="Commit the version bump. Implies --write.")
    parser.add_argument("--tag", action="store_true", help="Create the release tag. Requires --commit.")
    parser.add_argument("--push", action="store_true", help="Push the commit and tag. Requires --tag.")
    parser.add_argument("--no-lock", action="store_true", help="Do not run uv lock after changing the version.")
    args = parser.parse_args()

    if not args.bump and not args.version:
        parser.error("provide a bump part or --version")
    if args.bump and args.version:
        parser.error("provide either a bump part or --version, not both")
    if args.tag and not args.commit:
        parser.error("--tag requires --commit")
    if args.push and not args.tag:
        parser.error("--push requires --tag")

    package = PACKAGES[args.package]
    current = project_version(package.pyproject)
    new_version = args.version or bump_version(current, args.bump)
    tag = f"{package.tag_prefix}{new_version}"

    print(f"{package.name}: {current} -> {new_version}")
    print(f"tag: {tag}")

    ensure_tag_missing(tag)

    if not (args.write or args.commit):
        print("\nDry run only. Add --write to update files, or --commit --tag --push to release.")
        return 0

    assert_clean_worktree()
    set_project_version(package.pyproject, new_version)
    if not args.no_lock:
        refresh_lock()

    files = [str(package.pyproject.relative_to(ROOT))]
    if (ROOT / "uv.lock").exists():
        files.append("uv.lock")

    if args.commit:
        run(["git", "add", *files])
        run(["git", "commit", "-m", f"Release {package.name} {new_version}"])
        if args.tag:
            run(["git", "tag", tag])
            if args.push:
                branch = run(["git", "branch", "--show-current"]).stdout.strip()
                if not branch:
                    raise SystemExit("Cannot push automatically from a detached HEAD.")
                run(["git", "push", "origin", branch])
                run(["git", "push", "origin", tag])
                print(f"Pushed {branch} and {tag}. GitHub Actions will publish {package.name}.")
            else:
                print(f"Created tag {tag}. Push it with: git push origin {tag}")
    else:
        print("Updated files only. Review, then commit and tag when ready.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
