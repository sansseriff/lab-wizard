from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import logging
from textwrap import indent
from typing import Any, cast

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    model_to_commented_map,
    to_commented_yaml_value,
)
from lab_wizard.lib.utilities.params_discovery import get_parent_chain, get_type_to_module_map
from lab_wizard.wizard.backend.get_measurements import get_measurements, reqs_from_measurement
from lab_wizard.wizard.backend.models import Env, FilledReq

logger = logging.getLogger("lab_wizard.wizard.backend.project_generation")


class SelectedNodeRef(BaseModel):
    type: str
    key: str


class SelectedResource(BaseModel):
    variable_name: str
    type: str
    key: str
    channel_index: int | None = None
    # Optional explicit chain. Accepts either leaf->root or root->leaf.
    path: list[SelectedNodeRef] | None = None


class GenerateProjectRequest(BaseModel):
    measurement_name: str
    selected_resources: list[SelectedResource] = Field(default_factory=list)
    project_prefix: str | None = None


@dataclass
class _NodeRef:
    key: str
    params: Any
    parent: _NodeRef | None

    @property
    def type(self) -> str:
        return str(getattr(self.params, "type", ""))


def _walk_tree(instruments: dict[str, Any]) -> list[_NodeRef]:
    out: list[_NodeRef] = []

    def _recurse(key: str, params: Any, parent: _NodeRef | None) -> None:
        node = _NodeRef(key=key, params=params, parent=parent)
        out.append(node)
        for child_key, child_params in (getattr(params, "children", {}) or {}).items():
            _recurse(str(child_key), child_params, node)

    for top_key, top_params in instruments.items():
        _recurse(str(top_key), top_params, None)
    return out


def _normalize_path(sel: SelectedResource) -> list[SelectedNodeRef] | None:
    if not sel.path:
        return None
    if not sel.path:
        return None
    # Canonicalize to leaf->root shape.
    if sel.path[0].type == sel.type and sel.path[0].key == sel.key:
        return sel.path
    if sel.path[-1].type == sel.type and sel.path[-1].key == sel.key:
        return list(reversed(sel.path))
    raise ValueError(
        f"path for {sel.variable_name} must include leaf ({sel.type}, {sel.key})"
    )


def _node_lineage_leaf_to_root(node: _NodeRef) -> list[_NodeRef]:
    out: list[_NodeRef] = []
    cur: _NodeRef | None = node
    while cur is not None:
        out.append(cur)
        cur = cur.parent
    return out


def _validate_parent_chain(leaf: _NodeRef) -> None:
    actual = _node_lineage_leaf_to_root(leaf)
    actual_types = [n.type for n in actual]
    expected_types = [leaf.type] + get_parent_chain(leaf.type)
    if actual_types != expected_types:
        raise ValueError(
            "Tree lineage does not match parent_class chain for leaf "
            f"{leaf.type}:{leaf.key}. Actual={actual_types}, expected={expected_types}"
        )


def _resolve_selection_node(
    sel: SelectedResource,
    all_nodes: list[_NodeRef],
) -> _NodeRef:
    normalized = _normalize_path(sel)
    candidates = [n for n in all_nodes if n.type == sel.type and n.key == sel.key]
    if not candidates:
        raise ValueError(
            f"Selected leaf not found in configured tree: {sel.type}:{sel.key}"
        )
    if normalized is None:
        if len(candidates) > 1:
            raise ValueError(
                f"Selection {sel.type}:{sel.key} is ambiguous; provide explicit path"
            )
        _validate_parent_chain(candidates[0])
        return candidates[0]

    want = [(p.type, p.key) for p in normalized]
    for node in candidates:
        lineage = _node_lineage_leaf_to_root(node)
        have = [(n.type, n.key) for n in lineage]
        if have == want:
            _validate_parent_chain(node)
            return node

    raise ValueError(
        f"Could not match selection path for {sel.variable_name}. "
        f"Wanted {want}."
    )


def _clone_without_children(params: Any) -> Any:
    clone = params.model_copy(deep=True)
    if hasattr(clone, "children"):
        clone.children = {}  # type: ignore[attr-defined]
    return clone


def _build_subset_instruments_from_leaves(leaves: list[_NodeRef]) -> dict[str, Any]:
    roots: dict[str, Any] = {}

    for leaf in leaves:
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
        if not chain:
            continue

        root_node = chain[0]
        if root_node.key not in roots:
            roots[root_node.key] = _clone_without_children(root_node.params)
        parent_clone = roots[root_node.key]

        for node in chain[1:]:
            children = getattr(parent_clone, "children", None)
            if children is None:
                raise ValueError(
                    f"Node {type(parent_clone).__name__} unexpectedly has no children field"
                )
            existing = children.get(node.key)
            if existing is None:
                child_clone = _clone_without_children(node.params)
                children[node.key] = child_clone
                existing = child_clone
            parent_clone = existing

    return roots


def _sanitize_identifier(raw: str) -> str:
    out = re.sub(r"[^0-9a-zA-Z_]", "_", raw)
    out = re.sub(r"_+", "_", out).strip("_")
    if not out:
        out = "node"
    if out[0].isdigit():
        out = f"n_{out}"
    return out


def _format_measurement_slug(measurement_name: str) -> str:
    return _sanitize_identifier(measurement_name).lower()


def _measurement_prefix(measurement_name: str) -> str:
    parts = [p for p in measurement_name.split("_") if p]
    acronyms = {"iv": "IV", "pcr": "PCR"}
    return "".join(acronyms.get(p.lower(), p.capitalize()) for p in parts) or "Measurement"


def _short_type_token(type_str: str) -> str:
    """Short token used for generated variable names."""
    token = _sanitize_identifier(type_str).lower().split("_")[0]
    return token or "node"


def _type_info(type_str: str) -> tuple[str, str]:
    m = get_type_to_module_map().get(type_str)
    if m is None:
        raise ValueError(f"Unknown instrument type '{type_str}'")
    params_class = str(m["class_name"])
    inst_class = params_class[:-6] if params_class.endswith("Params") else params_class
    return str(m["module"]), params_class if params_class else inst_class


def _base_type_info(base_type: Any) -> tuple[str, str]:
    if hasattr(base_type, "__module__") and hasattr(base_type, "__name__"):
        return str(base_type.__module__), str(base_type.__name__)
    text = str(base_type)
    m = re.match(r"<class '([^']+)'>", text)
    if m:
        full = m.group(1)
        module, _, name = full.rpartition(".")
        if module and name:
            return module, name
    raise ValueError(f"Could not resolve base type import for {base_type!r}")


def _requirements_for_measurement(config_dir: Path, measurement_name: str) -> list[FilledReq]:
    lib_base = config_dir.resolve().parent / "lib"
    env = Env(base_dir=lib_base)
    all_meas = get_measurements(env)
    if measurement_name not in all_meas:
        raise ValueError(f"Unknown measurement: {measurement_name}")
    return reqs_from_measurement(all_meas[measurement_name])


def _setup_template_text(config_dir: Path, measurement_name: str) -> str:
    lab_wizard_root = Path(__file__).resolve().parents[2]
    template = (
        lab_wizard_root
        / "lib"
        / "measurements"
        / measurement_name
        / f"{measurement_name}_setup_template.py"
    )
    if not template.exists():
        raise ValueError(f"Missing setup template: {template}")
    return template.read_text(encoding="utf-8")


def _replace_wizard_block(template_text: str, block_name: str, content: str) -> str:
    pattern = re.compile(
        rf"(?P<indent>[ \t]*)# wizard:{re.escape(block_name)}:start\n"
        r"(?P<body>.*?)"
        rf"(?P=indent)# wizard:{re.escape(block_name)}:end",
        re.DOTALL,
    )
    m = pattern.search(template_text)
    if m is None:
        raise ValueError(f"Template missing wizard block '{block_name}'")
    indent_str = m.group("indent")
    new_middle = ""
    if content.strip():
        new_middle = indent(content.rstrip(), indent_str) + "\n"
    replacement = (
        f"{indent_str}# wizard:{block_name}:start\n"
        f"{new_middle}"
        f"{indent_str}# wizard:{block_name}:end"
    )
    return template_text[: m.start()] + replacement + template_text[m.end() :]


def _existing_import_symbols(template_text: str) -> set[str]:
    out: set[str] = set()
    for line in template_text.splitlines():
        m = re.match(r"^\s*from\s+\S+\s+import\s+(.+)$", line)
        if not m:
            continue
        for name in [n.strip() for n in m.group(1).split(",")]:
            if name:
                out.add(name)
    return out


def _compose_setup(
    measurement_name: str,
    selected_map: dict[str, _NodeRef],
    selected_channels: dict[str, int | None],
    requirements: list[FilledReq],
    template_text: str,
) -> str:
    if not requirements:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")
    missing = [r.variable_name for r in requirements if r.variable_name not in selected_map]
    if missing:
        raise ValueError(f"Missing required selections: {missing}")

    # Build unique creation steps per node path.
    created_inst: dict[tuple[tuple[str, str], ...], str] = {}
    created_params: dict[tuple[tuple[str, str], ...], str] = {}
    lines: list[str] = []
    import_pairs: set[tuple[str, str]] = set()
    used_names: dict[str, int] = {}

    def _alloc(base: str) -> str:
        count = used_names.get(base, 0) + 1
        used_names[base] = count
        return base if count == 1 else f"{base}_{count}"

    for leaf in selected_map.values():
        chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
        lineage_id: list[tuple[str, str]] = []
        for idx, node in enumerate(chain):
            lineage_id.append((node.type, node.key))
            key_t = tuple(lineage_id)
            if key_t in created_inst:
                continue

            module, params_cls = _type_info(node.type)
            inst_cls = params_cls[:-6] if params_cls.endswith("Params") else params_cls
            import_pairs.add((module, params_cls))
            import_pairs.add((module, inst_cls))

            token = _short_type_token(node.type)
            var_inst = _alloc(f"{token}_i")
            var_params = _alloc(f"{token}_p")
            created_inst[key_t] = var_inst
            created_params[key_t] = var_params

            if idx == 0:
                var_raw = _alloc(f"{token}_raw")
                lines.extend(
                    [
                        f"{var_raw} = exp.instruments[{node.key!r}]",
                        f"if not isinstance({var_raw}, {params_cls}):",
                        f'    raise TypeError("Expected {params_cls} at exp.instruments[{node.key!r}]")',
                        f"{var_params} = {var_raw}",
                        f"{var_inst} = {var_params}.create_inst()",
                        "",
                    ]
                )
            else:
                parent_id = tuple(lineage_id[:-1])
                parent_inst = created_inst[parent_id]
                parent_params = created_params[parent_id]
                lines.extend(
                    [
                        f"{var_params} = cast({params_cls}, {parent_params}.children[{node.key!r}])",
                        f"{var_inst} = {parent_inst}.add_child({var_params}, {node.key!r})",
                        "",
                    ]
                )

    def _final_expr(var_name: str, leaf: _NodeRef) -> str:
        chain = tuple((n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(leaf)))
        base_inst = created_inst[chain]
        ch_idx = selected_channels.get(var_name)
        ch_list = getattr(leaf.params, "channels", None)
        channels_list = cast(list[Any], ch_list) if isinstance(ch_list, list) else None
        if channels_list is not None and len(channels_list) > 1:
            if ch_idx is None:
                raise ValueError(
                    f"Selection for {var_name} uses multi-channel instrument "
                    f"{leaf.type}:{leaf.key}; channel_index is required"
                )
            if ch_idx < 0 or ch_idx >= len(channels_list):
                raise ValueError(
                    f"Invalid channel_index {ch_idx} for {leaf.type}:{leaf.key}; "
                    f"valid range is 0..{len(channels_list) - 1}"
                )
            return f"{base_inst}.channels[{ch_idx}]"
        if ch_idx is not None:
            raise ValueError(
                f"channel_index provided for {leaf.type}:{leaf.key}, but it is not multi-channel"
            )
        return base_inst

    assignment_lines: list[str] = []
    return_field_lines: list[str] = []
    resources_field_lines: list[str] = []
    for req in requirements:
        base_name = _base_type_info(req.base_type)[1]
        expr = _final_expr(req.variable_name, selected_map[req.variable_name])
        local_name = f"{req.variable_name}_1"
        assignment_lines.append(f"{local_name} = {expr}")
        return_field_lines.append(f"{req.variable_name}={local_name},")
        resources_field_lines.append(f"{req.variable_name}: {base_name}")

    existing_symbols = _existing_import_symbols(template_text)
    requirement_symbols = {_base_type_info(req.base_type)[1] for req in requirements}

    filtered_imports: list[str] = []
    seen_lines: set[str] = set()
    for mod, cls in sorted(import_pairs):
        if cls in requirement_symbols:
            continue
        if cls in existing_symbols:
            continue
        line = f"from {mod} import {cls}"
        if line in seen_lines:
            continue
        seen_lines.add(line)
        filtered_imports.append(line)

    uses_cast = any("cast(" in ln for ln in lines)
    imports_lines: list[str] = []
    if uses_cast and "cast" not in existing_symbols:
        imports_lines.append("from typing import cast")
    imports_lines.extend(filtered_imports)
    imports_block = "\n".join(imports_lines)
    instantiation_block = "\n".join(lines + assignment_lines).rstrip()

    rendered = template_text
    rendered = _replace_wizard_block(rendered, "imports", imports_block)
    rendered = _replace_wizard_block(
        rendered, "resource_fields", "\n".join(resources_field_lines)
    )
    rendered = _replace_wizard_block(rendered, "instantiation", instantiation_block)
    rendered = _replace_wizard_block(
        rendered, "return_fields", "\n".join(return_field_lines)
    )
    return rendered


def _default_project_yaml(measurement_name: str, instruments: dict[str, Any]) -> dict[str, Any]:
    exp_defaults: dict[str, Any]
    if measurement_name == "iv_curve":
        exp_defaults = {
            "type": "iv_curve",
            "start_voltage": 0.0,
            "stop_voltage": 1.4,
            "step_voltage": 0.005,
            "num_points": 100,
        }
    elif measurement_name == "pcr_curve":
        exp_defaults = {
            "type": "pcr_curve",
            "start_voltage": 0.0,
            "stop_voltage": 1.4,
            "step_voltage": 0.005,
            "photon_rate": 100000.0,
        }
    else:
        exp_defaults = {"type": measurement_name}

    return {
        "exp": exp_defaults,
        "device": {
            "type": "device",
            "name": "generated_device",
            "model": "unknown",
            "description": "Generated by wizard",
        },
        "saver": {
            "default": {
                "type": "file_saver",
                "file_path": "data/output.csv",
                "include_timestamp": True,
                "include_metadata": True,
            }
        },
        "plotter": {"default": {"type": "mpl_plotter", "figure_size": [8, 6], "dpi": 100}},
        "instruments": {
            key: model_to_commented_map(value, exclude_none=True)
            for key, value in instruments.items()
        },
    }


def _create_unique_project_dir(projects_dir: Path, prefix: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"{prefix}_{ts}"
    candidate = projects_dir / base_name
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=False)
        return candidate
    i = 1
    while True:
        attempt = projects_dir / f"{base_name}_{i}"
        if not attempt.exists():
            attempt.mkdir(parents=True, exist_ok=False)
            return attempt
        i += 1


def generate_measurement_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateProjectRequest,
) -> dict[str, Any]:
    logger.info("Generating project for measurement '%s'", req.measurement_name)
    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    selected_map: dict[str, _NodeRef] = {}
    selected_channels: dict[str, int | None] = {}
    for sel in req.selected_resources:
        selected_map[sel.variable_name] = _resolve_selection_node(sel, all_nodes)
        selected_channels[sel.variable_name] = sel.channel_index
    logger.debug(
        "Resolved %d selected resources for measurement '%s'",
        len(selected_map),
        req.measurement_name,
    )
    requirements = _requirements_for_measurement(config_dir, req.measurement_name)
    template_text = _setup_template_text(config_dir, req.measurement_name)

    subset = _build_subset_instruments_from_leaves(list(selected_map.values()))

    prefix = req.project_prefix or _format_measurement_slug(req.measurement_name)
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created project directory %s", project_dir)

    yaml_payload = _default_project_yaml(req.measurement_name, subset)
    yaml_path = project_dir / f"{project_dir.name}.yaml"
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    setup_code = _compose_setup(
        req.measurement_name,
        selected_map,
        selected_channels,
        requirements,
        template_text,
    )

    setup_path = project_dir / f"{req.measurement_name}_setup.py"
    setup_path.write_text(setup_code, encoding="utf-8")
    logger.info("Generated project artifacts yaml=%s setup=%s", yaml_path, setup_path)

    return {
        "status": "ok",
        "project_dir": str(project_dir),
        "project_name": project_dir.name,
        "measurement_name": req.measurement_name,
        "yaml_file": str(yaml_path),
        "setup_file": str(setup_path),
    }

