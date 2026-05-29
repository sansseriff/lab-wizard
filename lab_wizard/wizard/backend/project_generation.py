from __future__ import annotations

from pathlib import Path
import re
import logging
from textwrap import indent
from typing import Any, Literal, cast

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    model_to_commented_map,
    to_commented_yaml_value,
    instrument_hash,
)
from lab_wizard.lib.utilities.flat_resource_io import load_resources
from lab_wizard.wizard.backend.get_measurements import (
    get_measurements,
    reqs_from_measurement,
)
from lab_wizard.wizard.backend.models import Env, FilledReq
from lab_wizard.wizard.backend.python_formatting import format_python_code
from lab_wizard.wizard.backend._generation_common import (
    BaseSelection,
    SelectedNodeRef,
    _NodeRef,
    _build_subset_instruments_from_selected_nodes,
    _compose_pedagogical_embedded,
    _compose_pedagogical_yaml_expanded,
    _create_unique_project_dir,
    _node_lineage_leaf_to_root,
    _resolve_selection_node,
    _sanitize_identifier,
    _short_type_token,
    _type_info,
    _walk_tree,
)

logger = logging.getLogger("lab_wizard.wizard.backend.project_generation")


class SelectedResource(BaseSelection):
    """A single user selection.

    For ``resource_kind="instrument"`` the ``path`` and ``channel_index`` fields
    are used as before.  For savers/plotters they are ignored — the lookup uses
    just ``type`` + ``key`` against the global registry.  The same
    ``variable_name`` may appear multiple times for list-typed fields like
    ``savers: list[GenericSaver]`` (one entry per picked instance).
    """

    resource_kind: Literal["instrument", "saver", "plotter"] = "instrument"
    channel_index: int | None = None


class GenerateProjectRequest(BaseModel):
    measurement_name: str
    selected_resources: list[SelectedResource] = Field(default_factory=list)
    project_prefix: str | None = None
    generation_style: str = "production"


def _format_measurement_slug(measurement_name: str) -> str:
    return _sanitize_identifier(measurement_name).lower()


def _measurement_prefix(measurement_name: str) -> str:
    parts = [p for p in measurement_name.split("_") if p]
    acronyms = {"iv": "IV", "pcr": "PCR"}
    return (
        "".join(acronyms.get(p.lower(), p.capitalize()) for p in parts) or "Measurement"
    )


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


def _requirements_for_measurement(
    config_dir: Path, measurement_name: str
) -> list[FilledReq]:
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


def _split_requirements(
    reqs: list[FilledReq],
) -> tuple[list[FilledReq], list[FilledReq], list[FilledReq]]:
    instruments: list[FilledReq] = []
    savers: list[FilledReq] = []
    plotters: list[FilledReq] = []
    for r in reqs:
        if r.resource_kind == "saver":
            savers.append(r)
        elif r.resource_kind == "plotter":
            plotters.append(r)
        else:
            instruments.append(r)
    return instruments, savers, plotters


def _split_selections(
    sels: list[SelectedResource],
) -> tuple[list[SelectedResource], list[SelectedResource], list[SelectedResource]]:
    inst_sels: list[SelectedResource] = []
    saver_sels: list[SelectedResource] = []
    plotter_sels: list[SelectedResource] = []
    for s in sels:
        if s.resource_kind == "saver":
            saver_sels.append(s)
        elif s.resource_kind == "plotter":
            plotter_sels.append(s)
        else:
            inst_sels.append(s)
    return inst_sels, saver_sels, plotter_sels


def _flat_subset(
    selections: list[SelectedResource],
    registry: dict[str, Any],
    kind: str,
) -> dict[str, Any]:
    """Collect just the registry entries the user selected, by key."""
    out: dict[str, Any] = {}
    for sel in selections:
        if sel.key not in registry:
            raise ValueError(
                f"Selected {kind} '{sel.key}' (type={sel.type}) is not configured. "
                f"Available {kind}s: {sorted(registry.keys())}"
            )
        out[sel.key] = registry[sel.key]
    return out


def _saver_var_name(key: str) -> str:
    return f"saver_{_sanitize_identifier(key).lower()}"


def _plotter_var_name(key: str) -> str:
    return f"plotter_{_sanitize_identifier(key).lower()}"


def _flat_resource_codegen(
    selections: list[SelectedResource],
    kind: Literal["saver", "plotter"],
) -> tuple[list[tuple[str, str]], list[str], dict[str, str]]:
    """Generate import pairs, instantiation lines, and key-to-var-name map for
    a list of saver or plotter selections."""
    import_pairs: list[tuple[str, str]] = []
    inst_lines: list[str] = []
    var_names: dict[str, str] = {}
    seen_imports: set[tuple[str, str]] = set()
    var_alloc = _saver_var_name if kind == "saver" else _plotter_var_name

    for sel in selections:
        module, params_cls = _type_info(sel.type, kind=kind)
        runtime_cls = params_cls[:-6] if params_cls.endswith("Params") else params_cls
        if (module, runtime_cls) not in seen_imports:
            import_pairs.append((module, runtime_cls))
            seen_imports.add((module, runtime_cls))
        var = var_alloc(sel.key)
        var_names[sel.key] = var
        inst_lines.append(f"{var} = {runtime_cls}.from_config(resources, key={sel.key!r})")

    return import_pairs, inst_lines, var_names


def _format_resource_field_line(req: FilledReq) -> str:
    base_name = _base_type_info(req.base_type)[1]
    if req.is_list:
        return f"{req.variable_name}: list[{base_name}]"
    return f"{req.variable_name}: {base_name}"


def _format_return_field_line(req: FilledReq, vars_: list[str]) -> str:
    if req.is_list:
        items = ", ".join(vars_)
        return f"{req.variable_name}=[{items}],"
    if not vars_:
        raise ValueError(f"No selection provided for {req.variable_name}")
    return f"{req.variable_name}={vars_[0]},"


def _compose_setup(
    measurement_name: str,
    inst_selected_map: dict[str, _NodeRef],
    inst_selected_channels: dict[str, int | None],
    instrument_reqs: list[FilledReq],
    saver_reqs: list[FilledReq],
    plotter_reqs: list[FilledReq],
    saver_selections: list[SelectedResource],
    plotter_selections: list[SelectedResource],
    template_text: str,
    generation_style: str = "production",
) -> str:
    if not instrument_reqs and not saver_reqs and not plotter_reqs:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")

    missing = [
        r.variable_name
        for r in instrument_reqs
        if r.variable_name not in inst_selected_map
    ]
    if missing:
        raise ValueError(f"Missing required selections: {missing}")

    instrument_lines: list[str] = []
    instrument_import_pairs: set[tuple[str, str]] = set()

    # Saver / plotter codegen
    saver_imports, saver_inst_lines, saver_vars = _flat_resource_codegen(
        saver_selections, "saver"
    )
    plotter_imports, plotter_inst_lines, plotter_vars = _flat_resource_codegen(
        plotter_selections, "plotter"
    )

    # Resource fields + return fields, in template field order
    resource_field_lines: list[str] = []
    return_field_lines: list[str] = []
    instrument_assignments: list[str] = []

    if generation_style in ("pedagogical_yaml_expanded", "pedagogical_embedded"):
        leaves = [inst_selected_map[req.variable_name] for req in instrument_reqs]
        selections = [
            SelectedResource(
                variable_name=req.variable_name,
                type=inst_selected_map[req.variable_name].type,
                key=inst_selected_map[req.variable_name].key,
                channel_index=inst_selected_channels.get(req.variable_name),
            )
            for req in instrument_reqs
        ]
        if generation_style == "pedagogical_yaml_expanded":
            instrument_lines, imports, final_exprs = _compose_pedagogical_yaml_expanded(
                selections=selections,
                var_names=[req.variable_name for req in instrument_reqs],
                leaves=leaves,
            )
        else:
            instrument_lines, imports, final_exprs = _compose_pedagogical_embedded(
                selections=selections,
                var_names=[req.variable_name for req in instrument_reqs],
                leaves=leaves,
            )
        instrument_import_pairs.update(imports)
        for req, expr in zip(instrument_reqs, final_exprs):
            local_name = f"{req.variable_name}_1"
            instrument_assignments.append(f"{local_name} = {expr}")
            resource_field_lines.append(_format_resource_field_line(req))
            return_field_lines.append(_format_return_field_line(req, [local_name]))
    else:
        created_inst: dict[tuple[tuple[str, str], ...], str] = {}
        used_names: dict[str, int] = {}

        def _alloc(base: str) -> str:
            count = used_names.get(base, 0) + 1
            used_names[base] = count
            return base if count == 1 else f"{base}_{count}"

        for leaf in inst_selected_map.values():
            chain = list(reversed(_node_lineage_leaf_to_root(leaf)))  # root -> leaf
            lineage_id: list[tuple[str, str]] = []
            for idx, node in enumerate(chain):
                lineage_id.append((node.type, node.key))
                key_t = tuple(lineage_id)
                if key_t in created_inst:
                    continue

                module, params_cls = _type_info(node.type)
                inst_cls = (
                    params_cls[:-6] if params_cls.endswith("Params") else params_cls
                )
                instrument_import_pairs.add((module, inst_cls))

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
                    instrument_lines.extend(
                        [
                            f"{var_inst} = {inst_cls}.from_config(resources, key={node_hash!r})",
                            "",
                        ]
                    )
                else:
                    parent_id = tuple(lineage_id[:-1])
                    parent_inst = created_inst[parent_id]
                    instrument_lines.extend(
                        [
                            f"{var_inst} = {inst_cls}.from_config({parent_inst}, key={node_hash!r})",
                            "",
                        ]
                    )

        def _final_expr(var_name: str, leaf: _NodeRef) -> str:
            chain = tuple(
                (n.type, n.key) for n in reversed(_node_lineage_leaf_to_root(leaf))
            )
            base_inst = created_inst[chain]
            ch_idx = inst_selected_channels.get(var_name)
            ch_list = getattr(leaf.params, "channels", None)
            channels_list = (
                cast(list[Any], ch_list) if isinstance(ch_list, list) else None
            )
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

        for req in instrument_reqs:
            leaf = inst_selected_map[req.variable_name]
            local_name = f"{req.variable_name}_1"
            instrument_assignments.append(
                f"{local_name} = {_final_expr(req.variable_name, leaf)}"
            )
            resource_field_lines.append(_format_resource_field_line(req))
            return_field_lines.append(_format_return_field_line(req, [local_name]))

    for req in saver_reqs:
        vars_ = [
            saver_vars[s.key]
            for s in saver_selections
            if s.variable_name == req.variable_name
        ]
        if req.is_list and not vars_:
            return_field_lines.append(_format_return_field_line(req, []))
            resource_field_lines.append(_format_resource_field_line(req))
            continue
        if not vars_:
            raise ValueError(f"No saver selected for variable '{req.variable_name}'")
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, vars_))

    for req in plotter_reqs:
        vars_ = [
            plotter_vars[s.key]
            for s in plotter_selections
            if s.variable_name == req.variable_name
        ]
        if req.is_list and not vars_:
            return_field_lines.append(_format_return_field_line(req, []))
            resource_field_lines.append(_format_resource_field_line(req))
            continue
        if not vars_:
            raise ValueError(f"No plotter selected for variable '{req.variable_name}'")
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, vars_))

    existing_symbols = _existing_import_symbols(template_text)

    filtered_imports: list[str] = []
    seen_lines: set[str] = set()
    # Skip imports for symbols already in the template (e.g. base classes from
    # the wizard:resource_fields annotations).
    skip_names = set(existing_symbols)
    for mod, cls in sorted(instrument_import_pairs):
        if cls in skip_names:
            continue
        line = f"from {mod} import {cls}"
        if line in seen_lines:
            continue
        seen_lines.add(line)
        filtered_imports.append(line)
    for mod, cls in saver_imports + plotter_imports:
        if cls in skip_names:
            continue
        line = f"from {mod} import {cls}"
        if line in seen_lines:
            continue
        seen_lines.add(line)
        filtered_imports.append(line)

    imports_block = "\n".join(filtered_imports)
    instantiation_lines = (
        instrument_lines
        + (["", "# savers"] if saver_inst_lines else [])
        + saver_inst_lines
        + (["", "# plotters"] if plotter_inst_lines else [])
        + plotter_inst_lines
        + (["", ""] if instrument_assignments else [])
        + instrument_assignments
    )
    instantiation_block = "\n".join(instantiation_lines).rstrip()

    rendered = template_text
    rendered = _replace_wizard_block(rendered, "imports", imports_block)
    rendered = _replace_wizard_block(
        rendered, "resource_fields", "\n".join(resource_field_lines)
    )
    rendered = _replace_wizard_block(rendered, "instantiation", instantiation_block)
    rendered = _replace_wizard_block(
        rendered, "return_fields", "\n".join(return_field_lines)
    )
    return rendered


def _compose_setup_from_attribute(
    measurement_name: str,
    inst_selected_map: dict[str, _NodeRef],
    inst_selected_channels: dict[str, int | None],
    instrument_reqs: list[FilledReq],
    saver_reqs: list[FilledReq],
    plotter_reqs: list[FilledReq],
    saver_selections: list[SelectedResource],
    plotter_selections: list[SelectedResource],
    template_text: str,
) -> str:
    """Generate setup using ``resources.from_attribute``.  Saver/plotter handling is the
    same as in ``_compose_setup`` (they're keyed by user-given name, not by
    attribute_name)."""
    if not instrument_reqs and not saver_reqs and not plotter_reqs:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")
    missing = [
        r.variable_name
        for r in instrument_reqs
        if r.variable_name not in inst_selected_map
    ]
    if missing:
        raise ValueError(f"Missing required selections: {missing}")

    saver_imports, saver_inst_lines, saver_vars = _flat_resource_codegen(
        saver_selections, "saver"
    )
    plotter_imports, plotter_inst_lines, plotter_vars = _flat_resource_codegen(
        plotter_selections, "plotter"
    )

    resource_field_lines: list[str] = []
    return_field_lines: list[str] = []
    instrument_assignments: list[str] = []

    for req in instrument_reqs:
        leaf = inst_selected_map[req.variable_name]
        ch_idx = inst_selected_channels.get(req.variable_name)
        if ch_idx is not None:
            ch_list = getattr(leaf.params, "channels", None)
            if isinstance(ch_list, list) and ch_idx < len(ch_list):
                attr_name = getattr(ch_list[ch_idx], "attribute_name", "") or ""
            else:
                attr_name = ""
        else:
            attr_name = getattr(leaf.params, "attribute_name", "") or ""

        if not attr_name:
            raise ValueError(
                f"from_attribute generation requires attribute_name to be set on "
                f"{leaf.type}:{leaf.key} (resource '{req.variable_name}'). "
                "Set it in the instrument config and regenerate."
            )

        local_name = f"{req.variable_name}_1"
        instrument_assignments.append(
            f"{local_name} = resources.from_attribute({attr_name!r})"
        )
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, [local_name]))

    for req in saver_reqs:
        vars_ = [
            saver_vars[s.key]
            for s in saver_selections
            if s.variable_name == req.variable_name
        ]
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, vars_))

    for req in plotter_reqs:
        vars_ = [
            plotter_vars[s.key]
            for s in plotter_selections
            if s.variable_name == req.variable_name
        ]
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, vars_))

    existing_symbols = _existing_import_symbols(template_text)
    filtered_imports: list[str] = []
    for mod, cls in saver_imports + plotter_imports:
        if cls in existing_symbols:
            continue
        filtered_imports.append(f"from {mod} import {cls}")
    imports_block = "\n".join(filtered_imports)

    instantiation_lines = (
        (["# savers"] if saver_inst_lines else [])
        + saver_inst_lines
        + (["", "# plotters"] if plotter_inst_lines else [])
        + plotter_inst_lines
        + (["", ""] if instrument_assignments else [])
        + instrument_assignments
    )
    instantiation_block = "\n".join(instantiation_lines).rstrip()

    rendered = template_text
    rendered = _replace_wizard_block(rendered, "imports", imports_block)
    rendered = _replace_wizard_block(
        rendered, "resource_fields", "\n".join(resource_field_lines)
    )
    rendered = _replace_wizard_block(rendered, "instantiation", instantiation_block)
    rendered = _replace_wizard_block(
        rendered, "return_fields", "\n".join(return_field_lines)
    )
    return rendered


def _measurement_param_defaults(measurement_name: str) -> dict[str, Any]:
    """Default ``measurement.params`` for a measurement, derived from its typed
    params model so the YAML and the schema can never drift.

    Each entry constructs the model with no overrides and dumps it to plain
    JSON-compatible values. ``model_validate`` on the same model round-trips it.
    """
    from lab_wizard.lib.measurements.iv_curve.iv_curve_params import IVCurveParams
    from lab_wizard.lib.measurements.pcr_curve.pcr_curve_params import PCRCurveParams

    params_models: dict[str, type[BaseModel]] = {
        "iv_curve": IVCurveParams,
        "pcr_curve": PCRCurveParams,
    }
    model = params_models.get(measurement_name)
    if model is None:
        return {}
    return model().model_dump(mode="json")


def _default_project_yaml(
    measurement_name: str,
    instruments: dict[str, Any],
    savers: dict[str, Any],
    plotters: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project": {
            "schema_version": 1,
            "measurement_type": measurement_name,
            "created_by": "lab_wizard",
        },
        "run": {
            "device": {
                "type": "device",
                "name": "generated_device",
                "model": "unknown",
                "description": "Generated by wizard",
            },
            "metadata": {
                "operator": None,
                "description": None,
                "tags": [],
            },
        },
        "measurement": {
            "params": _measurement_param_defaults(measurement_name),
        },
        "resources": {
            "savers": {
                key: model_to_commented_map(value, exclude_none=True)
                for key, value in savers.items()
            },
            "plotters": {
                key: model_to_commented_map(value, exclude_none=True)
                for key, value in plotters.items()
            },
            "instruments": {
                key: model_to_commented_map(value, exclude_none=True)
                for key, value in instruments.items()
            },
        },
    }


def generate_measurement_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateProjectRequest,
) -> dict[str, Any]:
    logger.info("Generating project for measurement '%s'", req.measurement_name)
    if req.generation_style == "explicit":
        req.generation_style = "production"
    allowed_styles = {
        "production",
        "from_attribute",
        "pedagogical_yaml_expanded",
        "pedagogical_embedded",
    }
    if req.generation_style not in allowed_styles:
        raise ValueError(f"Unknown generation_style: {req.generation_style}")

    instrument_sels, saver_sels, plotter_sels = _split_selections(
        req.selected_resources
    )
    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    inst_selected_map: dict[str, _NodeRef] = {}
    inst_selected_channels: dict[str, int | None] = {}
    for sel in instrument_sels:
        inst_selected_map[sel.variable_name] = _resolve_selection_node(sel, all_nodes)
        inst_selected_channels[sel.variable_name] = sel.channel_index

    requirements = _requirements_for_measurement(config_dir, req.measurement_name)
    instrument_reqs, saver_reqs, plotter_reqs = _split_requirements(requirements)
    template_text = _setup_template_text(config_dir, req.measurement_name)

    instruments_subset = _build_subset_instruments_from_selected_nodes(
        [
            (leaf, inst_selected_channels.get(variable_name))
            for variable_name, leaf in inst_selected_map.items()
        ]
    )

    saver_registry = load_resources(config_dir, "saver")
    plotter_registry = load_resources(config_dir, "plotter")
    savers_subset = _flat_subset(saver_sels, saver_registry, "saver")
    plotters_subset = _flat_subset(plotter_sels, plotter_registry, "plotter")

    prefix = req.project_prefix or _format_measurement_slug(req.measurement_name)
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created project directory %s", project_dir)

    yaml_payload = _default_project_yaml(
        req.measurement_name,
        instruments_subset,
        savers_subset,
        plotters_subset,
    )
    yaml_path = project_dir / f"{project_dir.name}.yaml"
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    if req.generation_style == "from_attribute":
        setup_code = _compose_setup_from_attribute(
            req.measurement_name,
            inst_selected_map,
            inst_selected_channels,
            instrument_reqs,
            saver_reqs,
            plotter_reqs,
            saver_sels,
            plotter_sels,
            template_text,
        )
    else:
        setup_code = _compose_setup(
            req.measurement_name,
            inst_selected_map,
            inst_selected_channels,
            instrument_reqs,
            saver_reqs,
            plotter_reqs,
            saver_sels,
            plotter_sels,
            template_text,
            generation_style=req.generation_style,
        )

    setup_path = project_dir / f"{req.measurement_name}_setup.py"
    setup_code = format_python_code(setup_code)
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
