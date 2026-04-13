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
from typing import Any, cast

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.utilities.config_io import (
    instrument_hash,
    load_instruments,
    model_to_commented_map,
    to_commented_yaml_value,
)
from lab_wizard.wizard.backend._generation_common import (
    BaseSelection,
    _NodeRef,
    _build_subset_instruments_from_leaves,
    _create_unique_project_dir,
    _node_lineage_leaf_to_root,
    _resolve_selection_node,
    _sanitize_identifier,
    _short_type_token,
    _type_info,
    _walk_tree,
)

logger = logging.getLogger("lab_wizard.wizard.backend.custom_resource_generation")


class CustomResourceSelection(BaseSelection):
    # ``None`` means "use the whole instrument" (or single-channel instrument).
    channel_index: int | None = None


class GenerateCustomResourceRequest(BaseModel):
    selections: list[CustomResourceSelection] = Field(default_factory=list)
    project_prefix: str | None = None
    generation_style: str = "explicit"  # "explicit" | "from_attribute"
    file_style: str = "dataclass"  # "dataclass" | "simple"
    resource_class_name: str = "CustomResources"


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
        ch_list = getattr(leaf.params, "channels", None)
        if isinstance(ch_list, list) and 0 <= channel_index < len(ch_list):
            return getattr(ch_list[channel_index], "attribute_name", "") or ""
        return ""
    return getattr(leaf.params, "attribute_name", "") or ""


def _validate_channel(leaf: _NodeRef, channel_index: int | None, var_name: str) -> None:
    ch_list = getattr(leaf.params, "channels", None)
    channels_list = cast(list[Any], ch_list) if isinstance(ch_list, list) else None
    if channel_index is None:
        return
    if channels_list is None:
        raise ValueError(
            f"channel_index provided for {leaf.type}:{leaf.key} (resource '{var_name}'), "
            "but the instrument has no channels."
        )
    if channel_index < 0 or channel_index >= len(channels_list):
        raise ValueError(
            f"Invalid channel_index {channel_index} for {leaf.type}:{leaf.key} "
            f"(resource '{var_name}'); valid range is 0..{len(channels_list) - 1}"
        )


def _compose_explicit(
    selections: list[CustomResourceSelection],
    var_names: list[str],
    leaves: list[_NodeRef],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """Build the instantiation lines, imports, and per-selection final exprs.

    Mirrors the logic in :func:`project_generation._compose_setup` but is
    decoupled from measurement requirements.
    """

    created_inst: dict[tuple[tuple[str, str], ...], str] = {}
    instantiation_lines: list[str] = []
    import_pairs: set[tuple[str, str]] = set()
    used_inst_names: dict[str, int] = {}

    def _alloc(base: str) -> str:
        count = used_inst_names.get(base, 0) + 1
        used_inst_names[base] = count
        return base if count == 1 else f"{base}_{count}"

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
        lineage_id: list[tuple[str, str]] = []
        for idx, node in enumerate(chain):
            lineage_id.append((node.type, node.key))
            key_t = tuple(lineage_id)
            if key_t in created_inst:
                continue

            module, params_cls = _type_info(node.type)
            inst_cls = params_cls[:-6] if params_cls.endswith("Params") else params_cls
            import_pairs.add((module, inst_cls))

            token = _short_type_token(node.type)
            var_inst = _alloc(f"{token}_i")
            created_inst[key_t] = var_inst

            node_key_fields = (
                node.params.key_fields()
                if hasattr(node.params, "key_fields")
                else node.key
            )
            node_hash = (
                instrument_hash(node.type, node_key_fields)
                if node_key_fields
                else node.key
            )

            if idx == 0:
                instantiation_lines.append(
                    f"{var_inst} = {inst_cls}.from_config(exp, key={node_hash!r})"
                )
            else:
                parent_id = tuple(lineage_id[:-1])
                parent_inst = created_inst[parent_id]
                instantiation_lines.append(
                    f"{var_inst} = {inst_cls}.from_config({parent_inst}, key={node_hash!r})"
                )

    final_exprs: list[str] = []
    for sel, var_name, leaf in zip(selections, var_names, leaves):
        chain_key = tuple(
            (n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(leaf))
        )
        base_inst = created_inst[chain_key]
        _validate_channel(leaf, sel.channel_index, var_name)
        if sel.channel_index is not None:
            final_exprs.append(f"{base_inst}.channels[{sel.channel_index}]")
        else:
            final_exprs.append(base_inst)

    sorted_imports = sorted(import_pairs)
    return instantiation_lines, sorted_imports, final_exprs


def _compose_from_attribute(
    selections: list[CustomResourceSelection],
    var_names: list[str],
    leaves: list[_NodeRef],
) -> tuple[list[str], list[tuple[str, str]], list[str]]:
    """``exp.from_attribute("name")`` based generation. No instrument imports."""

    final_exprs: list[str] = []
    for sel, var_name, leaf in zip(selections, var_names, leaves):
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
        final_exprs.append(f"exp.from_attribute({attr_name!r})")

    return [], [], final_exprs


# ---------------------------------------------------------------------------
# File-shape rendering
# ---------------------------------------------------------------------------


_HEADER = '"""Generated by Lab Wizard — create custom resource."""\n'


def _render_imports(import_pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"from {mod} import {cls}" for mod, cls in import_pairs)


def _indent_block(lines: list[str], spaces: int) -> str:
    pad = " " * spaces
    return "\n".join(f"{pad}{ln}" for ln in lines)


def _render_dataclass_file(
    *,
    class_name: str,
    var_names: list[str],
    instantiation_lines: list[str],
    final_exprs: list[str],
    import_pairs: list[tuple[str, str]],
) -> str:
    imports_block = _render_imports(import_pairs)
    field_lines = [f"{name}: Any" for name in var_names]
    assign_lines = [f"{name} = {expr}" for name, expr in zip(var_names, final_exprs)]
    return_kwargs = [f"{name}={name}," for name in var_names]

    body_lines = instantiation_lines + assign_lines

    parts = [
        _HEADER,
        "from dataclasses import dataclass",
        "from pathlib import Path",
        "from typing import Any",
        "",
        "import yaml",
        "",
        "from lab_wizard.lib.utilities.model_tree import Exp",
    ]
    if imports_block:
        parts.append(imports_block)
    parts.extend(
        [
            "",
            "",
            "@dataclass",
            f"class {class_name}:",
            _indent_block(field_lines, 4),
            "",
            "",
            "def load_exp_from_yaml(yaml_path: str | Path):",
            '    with open(yaml_path, "r", encoding="utf-8") as f:',
            "        return Exp.model_validate(yaml.safe_load(f))",
            "",
            "",
            f"def create_custom_resources(exp: Exp) -> {class_name}:",
            _indent_block(body_lines, 4),
            f"    return {class_name}(",
            _indent_block(return_kwargs, 8),
            "    )",
            "",
            "",
            'if __name__ == "__main__":',
            "    this_file = Path(__file__).resolve()",
            '    exp = load_exp_from_yaml(this_file.with_suffix(".yaml"))',
            "    resources = create_custom_resources(exp)",
            "    print(resources)",
            "",
        ]
    )
    return "\n".join(parts)


def _render_simple_file(
    *,
    var_name: str,
    instantiation_lines: list[str],
    final_expr: str,
    import_pairs: list[tuple[str, str]],
) -> str:
    imports_block = _render_imports(import_pairs)
    body_lines = instantiation_lines + [f"{var_name} = {final_expr}"]

    parts = [
        _HEADER,
        "from pathlib import Path",
        "",
        "import yaml",
        "",
        "from lab_wizard.lib.utilities.model_tree import Exp",
    ]
    if imports_block:
        parts.append(imports_block)
    parts.extend(
        [
            "",
            "",
            "def load_exp_from_yaml(yaml_path: str | Path):",
            '    with open(yaml_path, "r", encoding="utf-8") as f:',
            "        return Exp.model_validate(yaml.safe_load(f))",
            "",
            "",
            "def create_custom_resource(exp: Exp):",
            _indent_block(body_lines, 4),
            f"    return {var_name}",
            "",
            "",
            'if __name__ == "__main__":',
            "    this_file = Path(__file__).resolve()",
            '    exp = load_exp_from_yaml(this_file.with_suffix(".yaml"))',
            "    resource = create_custom_resource(exp)",
            "    print(resource)",
            "",
        ]
    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# YAML snapshot
# ---------------------------------------------------------------------------


def _custom_resource_yaml(instruments: dict[str, Any]) -> dict[str, Any]:
    """Minimal project YAML for a custom resource — only the instruments tree.

    The ``Exp`` model still expects ``device``/``saver``/``plotter``/``exp``
    sections, so we emit harmless placeholders alongside the instruments.
    """
    return {
        "exp": {"type": "custom_resource"},
        "device": {
            "type": "device",
            "name": "custom_resource_device",
            "model": "unknown",
            "description": "Generated by wizard create_custom_resource",
        },
        "saver": {
            "default": {
                "type": "file_saver",
                "file_path": "data/output.csv",
                "include_timestamp": True,
                "include_metadata": True,
            }
        },
        "plotter": {
            "default": {"type": "mpl_plotter", "figure_size": [8, 6], "dpi": 100}
        },
        "instruments": {
            key: model_to_commented_map(value, exclude_none=True)
            for key, value in instruments.items()
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
    if req.generation_style not in ("explicit", "from_attribute"):
        raise ValueError(f"Unknown generation_style: {req.generation_style}")
    if req.file_style not in ("dataclass", "simple"):
        raise ValueError(f"Unknown file_style: {req.file_style}")
    if req.file_style == "simple" and len(req.selections) != 1:
        raise ValueError("Simple file style requires exactly one selection")

    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    leaves: list[_NodeRef] = [
        _resolve_selection_node(sel, all_nodes) for sel in req.selections
    ]
    var_names = _unique_var_names([sel.variable_name for sel in req.selections])

    logger.info(
        "Generating custom resource project: %d selections, style=%s/%s",
        len(req.selections),
        req.generation_style,
        req.file_style,
    )

    if req.generation_style == "explicit":
        instantiation_lines, import_pairs, final_exprs = _compose_explicit(
            req.selections, var_names, leaves
        )
    else:
        instantiation_lines, import_pairs, final_exprs = _compose_from_attribute(
            req.selections, var_names, leaves
        )

    if req.file_style == "dataclass":
        class_name = _sanitize_identifier(req.resource_class_name) or "CustomResources"
        setup_code = _render_dataclass_file(
            class_name=class_name,
            var_names=var_names,
            instantiation_lines=instantiation_lines,
            final_exprs=final_exprs,
            import_pairs=import_pairs,
        )
    else:
        setup_code = _render_simple_file(
            var_name=var_names[0],
            instantiation_lines=instantiation_lines,
            final_expr=final_exprs[0],
            import_pairs=import_pairs,
        )

    subset = _build_subset_instruments_from_leaves(leaves)
    prefix = (
        _sanitize_identifier(req.project_prefix or "custom_resource")
        or "custom_resource"
    )
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created custom resource project directory %s", project_dir)

    yaml_path = project_dir / f"{project_dir.name}.yaml"
    yaml_payload = _custom_resource_yaml(subset)
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    setup_path = project_dir / f"{project_dir.name}.py"
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
    }
