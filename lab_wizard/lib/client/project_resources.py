"""Where a generated project's instruments come from when it runs.

A project carries no instrument params (``plans/procedure_plan.md`` 5.4). It
records, in ``resources.instrument_sources``, the ``attribute_name`` of each
instrument and where it lives — ``local``, or a server named in
``config/remote/servers.yaml`` — and resolves each one at run time against a
tree it does not own:

* a **local** instrument against this workspace's ``config/instruments``, so
  readdressing a rack there, or changing its bench wiring, reaches every
  project that uses it;
* a **routed** instrument through its server.

Procedure params stay in the project YAML, frozen with the project.

The one exception is the embedded teaching style, whose setup file builds every
instrument from params written into the Python itself, so it runs outside any
workspace. Its YAML keeps a copy of those instruments, which is what its run
claims.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from lab_wizard.lib.client.composite_resources import LOCAL, CompositeResources
from lab_wizard.lib.client.local_claims import LocalTransportClaim
from lab_wizard.lib.client.server_discovery import find_workspace_config_dir, load_server_urls
from lab_wizard.lib.utilities.model_tree import ProjectConfig, ResourceConfig, _find_attribute_path


logger = logging.getLogger(__name__)

__all__ = ["WorkspaceNotFound", "is_embedded", "local_claims_for", "resource_source_for"]


class WorkspaceNotFound(RuntimeError):
    """A project that needs its workspace's instrument config was run outside one."""


def is_embedded(project: ProjectConfig) -> bool:
    """Whether this is an embedded-style project, carrying its own copy of its instruments."""
    return bool(project.resources.instruments)


def _workspace_instruments(project_dir: Path) -> dict[str, Any]:
    from lab_wizard.lib.utilities.config_io import load_instruments, validate_and_repair_hashes

    config_dir = find_workspace_config_dir(project_dir)
    if config_dir is None:
        raise WorkspaceNotFound(
            f"This project takes its instruments from a workspace's config/instruments, "
            f"but none was found in {project_dir} or any folder above it. Run it from "
            "inside the workspace it was generated in, or regenerate it with the "
            "embedded style to make it self-contained."
        )
    repaired, _changed = validate_and_repair_hashes(load_instruments(config_dir))
    return repaired


def _local_resources(project: ProjectConfig, project_dir: Path) -> ResourceConfig:
    """The workspace's instruments, with the project's routing."""
    return ResourceConfig(
        instruments=_workspace_instruments(project_dir),
        instrument_sources=project.resources.instrument_sources,
    )


def resource_source_for(project: ProjectConfig, project_dir: Path, *, remote: Optional[str] = None) -> Any:
    """The object a setup file's ``create_instrument_resources`` resolves against."""
    if remote:
        # Explicit override: every instrument through one server.
        return CompositeResources.all_remote(project, remote)
    if is_embedded(project):
        # Its setup file builds each instrument from the params in the Python.
        return project.resources
    return CompositeResources.from_project(
        project,
        server_urls=load_server_urls(project_dir),
        local=_local_resources(project, project_dir),
    )


def local_claims_for(
    project: ProjectConfig, project_dir: Path, *, owner: str, remote: Optional[str] = None
) -> list[LocalTransportClaim]:
    """Claims on the racks this process will open — only the ones the project uses.

    Taking the whole workspace tree would claim every rack in the lab for one
    measurement. Instead each local attribute is traced to its root, and only
    those roots are claimed.
    """
    if remote:
        return []
    if is_embedded(project):
        return [LocalTransportClaim(project.resources.instruments, owner=owner)]

    local = [attr for attr, source in project.resources.instrument_sources.items() if source == LOCAL]
    if not local:
        return []
    instruments = _workspace_instruments(project_dir)
    roots: dict[str, Any] = {}
    for attribute in local:
        found = _find_attribute_path(instruments, attribute)
        if found is None:
            raise ValueError(
                f"This project uses the instrument {attribute!r}, but no instrument in "
                "this workspace's config/instruments has that attribute_name. It may "
                "have been renamed or removed since the project was generated."
            )
        root_key, root_params = found[0][0]
        roots[root_key] = root_params
    return [LocalTransportClaim(roots, owner=owner)]
