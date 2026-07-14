"""Creation and discovery of user-owned Lab Wizard workspaces."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import tomllib

MANIFEST_NAME = "lab-wizard.toml"
WORKSPACE_ENV = "LAB_WIZARD_WORKSPACE"
CONFIG_SCHEMA_VERSION = 1
_CONFIG_DIRS = (
    "instruments",
    "measurements",
    "plotters",
    "savers",
    "server",
    "remote",
)


@dataclass(frozen=True)
class Workspace:
    """Resolved paths for one Lab Wizard workspace."""

    root: Path
    manifest: Path
    config_dir: Path
    projects_dir: Path
    logs_dir: Path
    config_schema: int


def _resolve_from_root(root: Path) -> Workspace:
    root = root.expanduser().resolve()
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise FileNotFoundError(f"No {MANIFEST_NAME} found at {manifest}")

    with manifest.open("rb") as stream:
        data = tomllib.load(stream)

    workspace_data = data.get("workspace") or {}
    version_data = data.get("lab_wizard") or {}
    if not isinstance(workspace_data, dict) or not isinstance(version_data, dict):
        raise ValueError(f"Invalid workspace manifest: {manifest}")

    def path_for(key: str, default: str) -> Path:
        value = workspace_data.get(key, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{manifest}: workspace.{key} must be a path string")
        path = Path(value).expanduser()
        return (root / path).resolve() if not path.is_absolute() else path.resolve()

    schema = version_data.get("config_schema", CONFIG_SCHEMA_VERSION)
    if not isinstance(schema, int) or schema < 1:
        raise ValueError(
            f"{manifest}: lab_wizard.config_schema must be a positive integer"
        )

    return Workspace(
        root=root,
        manifest=manifest,
        config_dir=path_for("config_dir", "config"),
        projects_dir=path_for("projects_dir", "projects"),
        logs_dir=path_for("logs_dir", "logs"),
        config_schema=schema,
    )


def load_workspace(path: str | Path) -> Workspace:
    """Load a workspace from its root directory or manifest path."""
    candidate = Path(path).expanduser()
    root = candidate.parent if candidate.name == MANIFEST_NAME else candidate
    return _resolve_from_root(root)


def find_workspace(
    start: str | Path | None = None, *, use_environment: bool = True
) -> Workspace | None:
    """Find the closest workspace, optionally honoring the environment override."""
    if use_environment:
        override = os.environ.get(WORKSPACE_ENV)
        if override:
            return load_workspace(override)

    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / MANIFEST_NAME).is_file():
            return _resolve_from_root(candidate)
    return None


def require_workspace(
    start: str | Path | None = None, *, use_environment: bool = True
) -> Workspace:
    """Resolve a workspace or raise an actionable error."""
    workspace = find_workspace(start, use_environment=use_environment)
    if workspace is None:
        raise FileNotFoundError(
            f"No {MANIFEST_NAME} found in this directory or its parents. "
            "Run 'wizard init .' to create a Lab Wizard workspace."
        )
    return workspace


def initialize_workspace(path: str | Path) -> tuple[Workspace, bool]:
    """Create an empty, user-owned workspace without overwriting files."""
    root = Path(path).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    manifest = root / MANIFEST_NAME
    created = not manifest.exists()
    if created:
        manifest.write_text(
            "[workspace]\n"
            'config_dir = "config"\n'
            'projects_dir = "projects"\n'
            'logs_dir = "logs"\n'
            "\n"
            "[lab_wizard]\n"
            f"config_schema = {CONFIG_SCHEMA_VERSION}\n",
            encoding="utf-8",
        )

    workspace = _resolve_from_root(root)
    workspace.config_dir.mkdir(parents=True, exist_ok=True)
    for name in _CONFIG_DIRS:
        (workspace.config_dir / name).mkdir(parents=True, exist_ok=True)
    workspace.projects_dir.mkdir(parents=True, exist_ok=True)
    workspace.logs_dir.mkdir(parents=True, exist_ok=True)
    return workspace, created


def clean_workspace(workspace: Workspace) -> list[Path]:
    """Remove workspace-managed state and its manifest.

    Every managed directory must resolve strictly below the workspace root.
    Validation happens before deletion, and the manifest is removed last.
    """
    targets = {workspace.config_dir, workspace.projects_dir, workspace.logs_dir}
    for target in targets:
        if target == workspace.root or not target.is_relative_to(workspace.root):
            raise ValueError(
                f"Refusing to clean path outside the workspace root: {target}"
            )
        if target.exists() and not target.is_dir():
            raise ValueError(
                f"Refusing to clean non-directory workspace path: {target}"
            )

    removed: list[Path] = []
    for target in sorted(targets, key=lambda path: len(path.parts), reverse=True):
        if target.exists():
            shutil.rmtree(target)
            removed.append(target)

    workspace.manifest.unlink()
    removed.append(workspace.manifest)
    return removed
