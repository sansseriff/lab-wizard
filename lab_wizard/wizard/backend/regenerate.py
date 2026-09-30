"""``wizard regenerate``: bring a project's Python up to date with its YAML.

A project's YAML is what is edited: its ``procedure:`` block, and which
instrument fills each role (``roles:``). Its Python follows:

* ``<name>_measurement.py`` is built from ``procedure:`` — which a run also
  does by itself when the block has changed (``lab_wizard.lib.project``);
* the setup file's ``wizard:roles`` block is written from ``roles:``: the class
  of each role's instrument, as this workspace's config or its server says it
  is. A run does *not* do this itself, because it needs the servers to answer,
  so a role added, or rebound to a different kind of instrument, is brought in
  here.

Nothing else in either file changes. An embedded project says everything in its
setup file and is regenerated from the wizard instead.
"""

from __future__ import annotations

import ast
import dataclasses
import textwrap
import typing
from pathlib import Path
from typing import Any, get_args, get_origin

from lab_wizard.lib.client.proxies.registry import proxy_class_for
from lab_wizard.lib.client.server_discovery import find_workspace_config_dir
from lab_wizard.lib.project import build_measurement_module, measurement_module
from lab_wizard.lib.project_module import module_path
from lab_wizard.lib.procedures.codegen import class_prefix
from lab_wizard.lib.utilities.config_io import load_instruments
from lab_wizard.lib.utilities.model_tree import RoleBinding, _find_attribute_path, load_project_config
from lab_wizard.lib.utilities.python_formatting import format_python_code
from lab_wizard.wizard.backend.instrument_sources import attributes_for_source
from lab_wizard.wizard.backend.project_generation import _replace_wizard_block
from lab_wizard.wizard.backend.setup_generation import ROLES_BLOCK, RoleType, roles_block

__all__ = ["RegenerateError", "regenerate_project"]

class RegenerateError(ValueError):
    """A project that cannot be brought up to date, and why."""


def _role_class(config_dir: Path, instruments: dict[str, Any], role: str, binding: RoleBinding) -> tuple[type, str]:
    """The class the setup declares for ``binding``, and a note to put beside it."""
    if binding.server is None:
        found = _find_attribute_path(instruments, binding.instrument)
        if found is None:
            raise RegenerateError(f"roles.{role}: no instrument in config/instruments is named {binding.instrument!r}")
        path, channel = found
        cls = type(path[-1][1]).resource_class()
        return (cls.channel_class if channel is not None else cls), ""
    offered = {a.get("attribute_name"): a for a in attributes_for_source(config_dir, binding.server)}
    entry = offered.get(binding.instrument)
    if entry is None:
        raise RegenerateError(f"roles.{role}: server {binding.server!r} offers no instrument named {binding.instrument!r}")
    hint = entry.get("type_hint")
    note = f"through {binding.server}, where it is a {hint}" if hint else f"through {binding.server}"
    return proxy_class_for(entry.get("behavior_abc")), note


def regenerate_project(project_dir: str | Path) -> list[str]:
    """Rebuild the project's measurement module and setup roles; returns what was done."""
    directory = Path(project_dir).resolve()
    yaml_path = directory / f"{directory.name}.yaml"
    if not yaml_path.is_file():
        raise RegenerateError(f"{directory} is not a project: it has no {yaml_path.name}")
    project = load_project_config(yaml_path)
    if project.project.style == "embedded":
        raise RegenerateError(
            "an embedded project says everything in its setup file; generate it again from the wizard"
        )
    config_dir = find_workspace_config_dir(directory)
    if config_dir is None:
        raise RegenerateError(f"{directory.name} is not inside a Lab Wizard workspace")

    done = []
    if build_measurement_module(directory, force=True):
        done.append(f"built {module_path(directory, project.measurement_type).name} from procedure:")

    # The roles the measurement declares, and whether each takes a list.
    module = measurement_module(directory)
    base = getattr(module, f"{class_prefix(project.measurement_type)}Resources", None) or module.Resources
    hints = typing.get_type_hints(base)
    wanted = [f.name for f in dataclasses.fields(base) if f.name != "params"]
    unbound = [role for role in wanted if role not in project.roles]
    if unbound:
        raise RegenerateError(f"roles: binds no instrument to {', '.join(unbound)}; add them to {yaml_path.name}")

    instruments = load_instruments(config_dir)
    types: list[RoleType] = []
    for role in wanted:
        binding = project.roles[role]
        is_list = get_origin(hints[role]) is list
        first = (binding if isinstance(binding, list) else [binding])[0]
        cls, note = _role_class(config_dir, instruments, role, first)
        types.append(RoleType(role=role, module=cls.__module__, name=cls.__name__, is_list=is_list, note=note))

    setup_path = next(iter(sorted(directory.glob("*_setup.py"))), None)
    if setup_path is None:
        raise RegenerateError(f"{directory.name} has no setup file")
    text = setup_path.read_text(encoding="utf-8")
    # The block helper indents content to the markers' own indentation.
    updated = _replace_wizard_block(text, ROLES_BLOCK, textwrap.dedent(roles_block(types)))
    # Import each role's class, if the file does not already.
    present = {
        (node.module, alias.name)
        for node in ast.walk(ast.parse(updated))
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
    }
    missing = sorted({(t.module, t.name) for t in types} - present)
    if missing:
        anchor = "from lab_wizard.lib.project import"
        lines = "".join(f"from {m} import {n}\n" for m, n in missing)
        updated = updated.replace(anchor, lines + anchor, 1) if anchor in updated else lines + updated
    if updated != text:
        setup_path.write_text(format_python_code(updated), encoding="utf-8")
        done.append(f"wrote the roles of {setup_path.name} from roles:")
    return done
