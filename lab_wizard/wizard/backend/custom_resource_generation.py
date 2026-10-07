"""Programmatic generation of standalone custom-resource setup files.

This module powers the *Create Custom Resource* workflow in the wizard GUI.
Unlike :mod:`project_generation`, no measurement template is involved — the
output ``.py`` file is built from a Python skeleton.  The user picks any
instruments / channels from the configured tree and the resulting file
exposes them either as a single returned object (when there is one
selection and *simple* style is chosen) or as fields on a generated
dataclass.
"""

from __future__ import annotations

from pathlib import Path
import logging
from typing import Any

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.utilities.python_formatting import format_python_code
from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    model_to_commented_map,
    save_instruments_to_config,
    to_commented_yaml_value,
)
from lab_wizard.wizard.backend.attribute_name_autogen import autogen_attribute_names
from lab_wizard.wizard.backend.instrument_sources import (
    LOCAL,
    attributes_for_source,
    ensure_source_registered,
    resolve_source_url,
)
from lab_wizard.wizard.backend.project_generation import (
    GenerationStyle,
    _refuse_embedded_through_server,
)
from lab_wizard.wizard.backend._generation_common import (
    BaseSelection,
    _NodeRef,
    _build_subset_instruments_from_selected_nodes,
    _compose_pedagogical_embedded,
    _create_unique_project_dir,
    _resolve_selection_node,
    _root_paths,
    _sanitize_identifier,
    _selected_runtime_imports,
    _selected_runtime_type,
    _walk_tree,
)
from lab_wizard.lib.instruments.general.behavior import behaviors

logger = logging.getLogger("lab_wizard.wizard.backend.custom_resource_generation")


class CustomResourceSelection(BaseSelection):
    # ``None`` means "use the whole instrument" (or single-channel instrument).
    channel_index: int | None = None
    # Which source owns this instrument: ``local``, or a name from
    # /api/instrument-sources.
    source: str = LOCAL
    # The ``attribute_name`` on that source. Required for a routed selection,
    # which has no params in this workspace to derive one from.
    attribute: str | None = None
    # Behavior ABC the source reported, used as the static type of a routed
    # resource — the runtime object is a proxy, so the concrete driver class
    # would be a lie that type-checks.
    behavior_abc: str | None = None
    # A routed selection may come from a flat leaf list with no tree position.
    type: str = ""
    key: str = ""


class GenerateCustomResourceRequest(BaseModel):
    selections: list[CustomResourceSelection] = Field(default_factory=list)
    project_prefix: str | None = None
    generation_style: GenerationStyle = "production"
    file_style: str = "dataclass"  # "dataclass" | "simple"
    resource_class_name: str = "CustomResources"
    persist_attribute_names: bool = False


# ---------------------------------------------------------------------------
# Codegen helpers
# ---------------------------------------------------------------------------


def _unique_var_names(raw_names: list[str]) -> list[str]:
    """Sanitize and de-duplicate the user-supplied variable names."""
    out: list[str] = []
    used: dict[str, int] = {}
    for raw in raw_names:
        base = _sanitize_identifier(raw)
        count = used.get(base, 0) + 1
        used[base] = count
        out.append(base if count == 1 else f"{base}_{count}")
    return out


def _channel_attribute_name(leaf: _NodeRef, channel_index: int | None) -> str:
    if channel_index is not None:
        ch_map = getattr(leaf.params, "channels", None)
        if isinstance(ch_map, dict) and channel_index in ch_map:
            return getattr(ch_map[channel_index], "attribute_name", "") or ""
        return ""
    return getattr(leaf.params, "attribute_name", "") or ""


def _validate_channel(leaf: _NodeRef, channel_index: int | None, var_name: str) -> None:
    if channel_index is None:
        return
    ch_map = getattr(leaf.params, "channels", None)
    if not isinstance(ch_map, dict):
        raise ValueError(
            f"channel_index provided for {leaf.type}:{leaf.key} (resource '{var_name}'), "
            "but the instrument has no channels."
        )
    num_channels = int(getattr(type(leaf.params), "num_channels", 0) or 0)
    if channel_index < 0 or channel_index >= num_channels:
        raise ValueError(
            f"Invalid channel_index {channel_index} for {leaf.type}:{leaf.key} "
            f"(resource '{var_name}'); valid range is 0..{num_channels - 1}"
        )


def _behavior_import(name: str | None) -> tuple[str, str] | None:
    """``(module, class_name)`` for a behavior ABC, from the behavior registry.

    Derived rather than tabulated, so a newly registered behavior is annotatable
    in generated files without an edit here.
    """
    if not name:
        return None

    for registered, cls in behaviors():
        if registered == name:
            return (cls.__module__, registered)
    return None


def _compose_from_attribute(
    selections: list[CustomResourceSelection],
    var_names: list[str],
    leaves: list[Any],
) -> tuple[list[str], list[tuple[str, str]], list[str], list[str]]:
    """``resource_config.from_attribute("name")`` based generation. No instrument imports.

    A routed selection has no ``_NodeRef`` — there are no params for it in this
    workspace — so its attribute name comes straight off the selection and its
    static type is the behavior ABC the source reported. The concrete driver
    class is deliberately not claimed: the runtime object is a proxy.
    """

    final_exprs: list[str] = []
    final_types: list[str] = []
    behavior_imports: set[tuple[str, str]] = set()
    for sel, var_name, leaf in zip(selections, var_names, leaves):
        if leaf is None:
            final_exprs.append(f"resource_config.from_attribute({sel.attribute!r})")
            # The behavior ABC is the honest static type: the runtime object is a
            # proxy satisfying that interface, not the server's driver class.
            pair = _behavior_import(sel.behavior_abc)
            if pair is None:
                final_types.append("object")
            else:
                behavior_imports.add(pair)
                final_types.append(pair[1])
            continue
        _validate_channel(leaf, sel.channel_index, var_name)
        attr_name = _channel_attribute_name(leaf, sel.channel_index)
        if not attr_name:
            target = (
                f"channel {sel.channel_index} of {leaf.type}:{leaf.key}"
                if sel.channel_index is not None
                else f"{leaf.type}:{leaf.key}"
            )
            raise ValueError(
                f"from_attribute generation requires attribute_name to be set on "
                f"{target} (resource '{var_name}'). Set it in the instrument config "
                "and try again."
            )
        final_exprs.append(f"resource_config.from_attribute({attr_name!r})")
        final_types.append(_selected_runtime_type(leaf, sel.channel_index))

    # Routed selections have no node to derive a driver import from.
    local = [(sel, leaf) for sel, leaf in zip(selections, leaves) if leaf is not None]
    imports = _selected_runtime_imports(
        selections=[sel for sel, _ in local], leaves=[leaf for _, leaf in local]
    )
    return ([], sorted(imports | behavior_imports), final_exprs, final_types)


# ---------------------------------------------------------------------------
# File-shape rendering
# ---------------------------------------------------------------------------


_HEADER = '"""Generated by Lab Wizard — create custom resource."""\n'


def _render_imports(import_pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"from {mod} import {cls}" for mod, cls in import_pairs)


def _indent_block(lines: list[str], spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{ln}" for ln in lines)


def _preamble(embedded: bool, import_pairs: list[tuple[str, str]]) -> list[str]:
    """Imports shared by both file shapes.

    The production file resolves its instruments the way a generated project
    does — by name, against the workspace's ``config/instruments`` or the server
    that owns them. The embedded file carries its own params and reads nothing.
    """
    parts = [_HEADER]
    if not embedded:
        parts.extend(
            [
                "from pathlib import Path",
                "",
                "from lab_wizard.lib.client.project_resources import resource_source_for",
                "from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config",
            ]
        )
    imports_block = _render_imports(import_pairs)
    if imports_block:
        parts.append(imports_block)
    return parts


def _main_block(call: str, name: str, embedded: bool) -> list[str]:
    if embedded:
        return ['if __name__ == "__main__":', f"    {name} = {call}()", f"    print({name})", ""]
    return [
        'if __name__ == "__main__":',
        "    this_file = Path(__file__).resolve()",
        '    project = load_project_config(this_file.with_suffix(".yaml"))',
        f"    {name} = {call}(project, resource_source_for(project, this_file.parent))",
        f"    print({name})",
        "",
    ]


def _render_dataclass_file(
    *,
    class_name: str,
    var_names: list[str],
    instantiation_lines: list[str],
    final_exprs: list[str],
    final_types: list[str],
    import_pairs: list[tuple[str, str]],
    embedded: bool = False,
) -> str:
    field_lines = [f"{name}: {typ}" for name, typ in zip(var_names, final_types)]
    assign_lines = [
        f"{name}: {typ} = {expr}"
        for name, typ, expr in zip(var_names, final_types, final_exprs)
    ]
    body_lines = instantiation_lines + assign_lines

    parts = _preamble(embedded, import_pairs)
    parts.append("from dataclasses import dataclass")
    parts.extend(["", "", "@dataclass", f"class {class_name}:", _indent_block(field_lines, 4)])
    if embedded:
        parts.extend(["", "", f"def create_custom_resources() -> {class_name}:"])
    else:
        parts.extend(
            [
                "",
                "",
                f"def create_custom_resources(",
                "    project: ProjectConfig,",
                "    resource_source: object | None = None,",
                f") -> {class_name}:",
                "    resource_config = resource_source or project.resources",
            ]
        )
    parts.append(_indent_block(body_lines, 4))
    parts.extend(
        [f"    return {class_name}(", _indent_block([f"{n}={n}," for n in var_names], 8), "    )", "", ""]
    )
    parts.extend(_main_block("create_custom_resources", "resources", embedded))
    return "\n".join(parts)


def _render_simple_file(
    *,
    var_name: str,
    instantiation_lines: list[str],
    final_expr: str,
    final_type: str,
    import_pairs: list[tuple[str, str]],
    embedded: bool = False,
) -> str:
    body_lines = instantiation_lines + [f"{var_name}: {final_type} = {final_expr}"]

    parts = _preamble(embedded, import_pairs)
    if embedded:
        parts.extend(["", "", "def create_custom_resource():"])
    else:
        parts.extend(
            [
                "",
                "",
                "def create_custom_resource(",
                "    project: ProjectConfig,",
                "    resource_source: object | None = None,",
                "):",
                "    resource_config = resource_source or project.resources",
            ]
        )
    parts.append(_indent_block(body_lines, 4))
    parts.extend([f"    return {var_name}", "", ""])
    parts.extend(_main_block("create_custom_resource", "resource", embedded))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# YAML snapshot
# ---------------------------------------------------------------------------


def _custom_resource_yaml(
    instruments: dict[str, Any],
    instrument_sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Minimal project YAML for a custom resource: which instruments it uses.

    A production file names them — ``instrument_sources`` says which tree
    answers for each — and copies no params. The embedded escape hatch is the
    one that carries ``instruments``.
    """
    return {
        "project": {
            "schema_version": 1,
            "measurement_type": "custom_resource",
            "created_by": "lab_wizard",
        },
        # Who ran it, and why; recorded with every run.
        "run": {"operator": None, "notes": None},
        # The setup it runs on: its fields and mounted device go with each run.
        "setup": {"name": None, "needs": {}},
        "measurement": {"params": {}},
        "resources": {
            "instruments": {
                key: model_to_commented_map(value, exclude_none=True)
                for key, value in instruments.items()
            },
            # Omitted entirely when nothing is routed, so a local custom
            # resource's YAML is unchanged.
            **(
                {"instrument_sources": dict(instrument_sources)}
                if instrument_sources
                else {}
            ),
        },
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_custom_resource_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateCustomResourceRequest,
) -> dict[str, Any]:
    if not req.selections:
        raise ValueError("At least one selection is required")
    # Same two styles the measurement flow offers, and the same retirement
    # message for the third (procedure_plan.md 5.10).
    style = req.generation_style
    if req.file_style not in ("dataclass", "simple"):
        raise ValueError(f"Unknown file_style: {req.file_style}")
    if req.file_style == "simple" and len(req.selections) != 1:
        raise ValueError("Simple file style requires exactly one selection")
    _refuse_embedded_through_server(style, req.selections)

    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    # A routed selection has no node in this workspace's tree; ``None`` marks it
    # so the composers can tell the two apart.
    leaves: list[Any] = []
    instrument_sources: dict[str, str] = {}
    offered: dict[str, dict[str, Any]] = {}
    registered: dict[str, str] = {}
    for sel in req.selections:
        if sel.source == LOCAL:
            leaves.append(_resolve_selection_node(sel, all_nodes))
            continue
        if not sel.attribute:
            raise ValueError(
                f"Selection '{sel.variable_name}' comes from source {sel.source!r} "
                "but carries no attribute name."
            )
        if sel.source not in offered:
            offered[sel.source] = {
                a.get("attribute_name"): a
                for a in attributes_for_source(config_dir, sel.source)
                if a.get("attribute_name")
            }
            registered[sel.source] = ensure_source_registered(
                config_dir, sel.source, resolve_source_url(config_dir, sel.source)
            )
        entry = offered[sel.source].get(sel.attribute)
        if entry is None:
            raise ValueError(
                f"Source {sel.source!r} no longer offers an instrument named "
                f"{sel.attribute!r} (needed for '{sel.variable_name}'). Reload and "
                "pick again."
            )
        # Trust the source's own answer over whatever the picker sent.
        sel.behavior_abc = entry.get("behavior_abc") or sel.behavior_abc
        instrument_sources[sel.attribute] = registered[sel.source]
        leaves.append(None)

    embedded = style == "pedagogical_embedded"

    var_names = _unique_var_names([sel.variable_name for sel in req.selections])

    logger.info(
        "Generating custom resource project: %d selections, style=%s/%s",
        len(req.selections),
        style,
        req.file_style,
    )

    if embedded:
        instantiation_lines, import_pairs, final_exprs = _compose_pedagogical_embedded(
            selections=req.selections,
            var_names=var_names,
            leaves=leaves,
        )
        final_types = [
            _selected_runtime_type(leaf, sel.channel_index)
            for sel, leaf in zip(req.selections, leaves)
        ]
    else:
        # Every instrument is named, local ones included: the file resolves them
        # against the tree that owns them rather than a copy of its own.
        mutations = autogen_attribute_names(
            instruments,
            [
                (leaf, sel.channel_index)
                for sel, leaf in zip(req.selections, leaves)
                if leaf is not None
            ],
        )
        if mutations:
            logger.info(
                "Auto-generated %d attribute_name(s) for from_attribute generation",
                len(mutations),
            )
            if req.persist_attribute_names:
                save_instruments_to_config(instruments, config_dir)
                logger.info(
                    "Persisted auto-generated attribute_names to %s", config_dir
                )
        instantiation_lines, import_pairs, final_exprs, final_types = (
            _compose_from_attribute(req.selections, var_names, leaves)
        )
        for sel, leaf in zip(req.selections, leaves):
            if leaf is not None:
                instrument_sources[_channel_attribute_name(leaf, sel.channel_index)] = LOCAL

    if req.file_style == "dataclass":
        class_name = _sanitize_identifier(req.resource_class_name) or "CustomResources"
        setup_code = _render_dataclass_file(
            class_name=class_name,
            var_names=var_names,
            instantiation_lines=instantiation_lines,
            final_exprs=final_exprs,
            final_types=final_types,
            import_pairs=import_pairs,
            embedded=embedded,
        )
    else:
        setup_code = _render_simple_file(
            var_name=var_names[0],
            instantiation_lines=instantiation_lines,
            final_expr=final_exprs[0],
            final_type=final_types[0],
            import_pairs=import_pairs,
            embedded=embedded,
        )

    # A production file names its instruments and reads their params from the
    # tree that owns them, so it copies none. Only the embedded escape hatch
    # carries a copy, which is the reason to choose it.
    subset = (
        _build_subset_instruments_from_selected_nodes(
            [
                (leaf, sel.channel_index)
                for sel, leaf in zip(req.selections, leaves)
                if leaf is not None
            ]
        )
        if embedded
        else {}
    )
    prefix = (
        _sanitize_identifier(req.project_prefix or "custom_resource")
        or "custom_resource"
    )
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created custom resource project directory %s", project_dir)

    yaml_path = project_dir / f"{project_dir.name}.yaml"
    yaml_payload = _custom_resource_yaml(subset, instrument_sources)
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    setup_path = project_dir / f"{project_dir.name}.py"
    setup_code = format_python_code(setup_code)
    setup_path.write_text(setup_code, encoding="utf-8")
    logger.info(
        "Generated custom resource artifacts yaml=%s setup=%s", yaml_path, setup_path
    )

    return {
        "status": "ok",
        "project_dir": str(project_dir),
        "project_name": project_dir.name,
        "yaml_file": str(yaml_path),
        "setup_file": str(setup_path),
        "local_roots": _root_paths([leaf for leaf in leaves if leaf is not None]),
    }
