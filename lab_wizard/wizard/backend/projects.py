"""What the wizard has already generated, read back off disk.

Project creation previously ended by returning a path string, and nothing in the
UI referred to that project again — so the wizard was a generator rather than a
workbench. This reads `projects/` back so a generated project stays a thing you
can find.

Everything here is derived from files that generation already writes; no new
state is recorded. The project YAML carries `project.measurement_type`, a
`resources:` block naming the instruments the project was bound to, and an
`outputs:` block saying what its runs produce, which is enough to describe a
project without opening it.

A directory that is not a wizard project, or whose YAML is unreadable, is
reported with what could be determined rather than skipped: a project the user
can see on disk but not in this list would look like data loss.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

from lab_wizard.lib.utilities.model_tree import OutputsConfig


logger = logging.getLogger("lab_wizard.wizard.backend.projects")

__all__ = ["list_projects", "projects_referencing"]


def _read_project_yaml(project_dir: Path) -> Optional[dict[str, Any]]:
    """The project's own YAML, which generation names after the directory."""
    candidate = project_dir / f"{project_dir.name}.yaml"
    if not candidate.is_file():
        # Fall back to any single top-level YAML: a renamed directory should not
        # make its project unreadable.
        others = [p for p in project_dir.glob("*.yaml") if p.is_file()]
        if len(others) != 1:
            return None
        candidate = others[0]

    try:
        with candidate.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except Exception as exc:  # noqa: BLE001 - a malformed project is a real answer
        logger.info("Could not parse %s: %s", candidate, exc)
        return None

    return loaded if isinstance(loaded, dict) else None


def _instrument_names(resources: Any) -> list[str]:
    """The instruments the project is bound to, for a one-line summary."""
    if not isinstance(resources, dict):
        return []
    # A current project copies no instrument params; it names its instruments
    # in instrument_sources, and those names are what it is bound to.
    for key in ("instruments", "instrument_sources"):
        block = resources.get(key)
        if isinstance(block, dict) and block:
            return sorted(str(k) for k in block)
    return []


def _outputs(data: Any) -> dict[str, Any]:
    """The project's outputs: block, with defaults for what it leaves out."""
    try:
        return OutputsConfig.model_validate(data or {}).model_dump(mode="json")
    except ValueError:
        return OutputsConfig().model_dump(mode="json")


def list_projects(projects_dir: str | Path) -> list[dict[str, Any]]:
    """Every generated project, newest first.

    Ordered by directory mtime rather than by the timestamp in the name: a
    project prefix is user-supplied and need not contain one, so the name is not
    a reliable clock.
    """
    root = Path(projects_dir).expanduser()
    if not root.is_dir():
        return []

    projects: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "__")):
            continue

        data = _read_project_yaml(entry)
        project_block = (data or {}).get("project")
        project_block = project_block if isinstance(project_block, dict) else {}

        setup_files = sorted(p.name for p in entry.glob("*_setup.py"))

        try:
            created = datetime.fromtimestamp(entry.stat().st_mtime, timezone.utc)
            created_iso: Optional[str] = created.isoformat()
            sort_key = entry.stat().st_mtime
        except OSError:
            created_iso = None
            sort_key = 0.0

        projects.append(
            {
                "name": entry.name,
                "path": str(entry),
                "measurement": project_block.get("measurement_type"),
                "schema_version": project_block.get("schema_version"),
                "created": created_iso,
                "instruments": _instrument_names((data or {}).get("resources")),
                "outputs": _outputs((data or {}).get("outputs")),
                "setup_file": setup_files[0] if setup_files else None,
                # A directory with no readable project YAML is still listed, but
                # the UI needs to know not to promise anything about it.
                "readable": data is not None,
                "_sort": sort_key,
            }
        )

    projects.sort(key=lambda p: p["_sort"], reverse=True)
    for project in projects:
        project.pop("_sort", None)
    return projects


def projects_referencing(projects_dir: str | Path, attributes: set[str]) -> list[dict[str, Any]]:
    """Projects that name any of ``attributes`` as an instrument, newest first.

    A project resolves each instrument by ``attribute_name`` when it runs, so
    removing or renaming a named instrument breaks every project that uses it.
    This is what a confirm dialog lists before that happens. Projects that still
    carry their own instrument copy address it by hash and are unaffected.
    """
    if not attributes:
        return []
    root = Path(projects_dir).expanduser()
    if not root.is_dir():
        return []
    found: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith((".", "__")):
            continue
        data = _read_project_yaml(entry) or {}
        resources = data.get("resources")
        sources = resources.get("instrument_sources") if isinstance(resources, dict) else None
        if not isinstance(sources, dict):
            continue
        used = sorted(attributes & {str(k) for k in sources})
        if used:
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                mtime = 0.0
            found.append({"name": entry.name, "path": str(entry), "attributes": used, "_sort": mtime})
    found.sort(key=lambda p: p["_sort"], reverse=True)
    for project in found:
        project.pop("_sort", None)
    return found
