from __future__ import annotations

from pathlib import Path
import re
import logging
from textwrap import indent
from typing import Any, Literal, NamedTuple

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
    params_model_for_measurement,
    reqs_from_measurement,
)
from lab_wizard.wizard.backend.instrument_sources import (
    LOCAL,
    attributes_for_source,
    ensure_source_registered,
    resolve_source_url,
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
    # Which source owns this instrument: ``local``, or a name from
    # /api/instrument-sources. Savers and plotters are always local — they write
    # this machine's database and draw on this machine's screen.
    source: str = LOCAL
    # The ``attribute_name`` on that source. Required for a routed selection,
    # which has no params in this workspace to derive one from; ignored for a
    # local one, where it is read off the tree.
    attribute: str | None = None
    # A routed selection may come from a flat leaf list with no tree position,
    # so type/key are not always known here.
    type: str = ""
    key: str = ""


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


def _measurement_info(measurement_name: str):
    """Look up a discovered measurement by name, or raise."""
    lib_base = Path(__file__).resolve().parents[2] / "lib"
    all_meas = get_measurements(Env(base_dir=lib_base))
    if measurement_name not in all_meas:
        raise ValueError(f"Unknown measurement: {measurement_name}")
    return all_meas[measurement_name]


def _requirements_for_measurement(measurement_name: str) -> list[FilledReq]:
    return reqs_from_measurement(_measurement_info(measurement_name))


def _setup_template_text(measurement_name: str) -> str:
    template = (
        _measurement_info(measurement_name).measurement_dir
        / f"{measurement_name}_setup_template.py"
    )
    if not template.exists():
        raise ValueError(f"Missing setup template: {template}")
    return template.read_text(encoding="utf-8")


def _measurement_source_text(measurement_name: str) -> str:
    """Return the runnable measurement source shipped with a project.

    The setup template describes resource wiring, while this file contains the
    procedure itself. Keeping both in the generated directory makes the
    generated project the editable unit users run, instead of silently running
    a different copy from the installed ``lab_wizard`` package.
    """
    source = (
        _measurement_info(measurement_name).measurement_dir
        / f"{measurement_name}.py"
    )
    if not source.exists():
        raise ValueError(f"Missing measurement source: {source}")
    return source.read_text(encoding="utf-8")


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
        inst_lines.append(
            f"{var} = {runtime_cls}.from_config(resources, key={sel.key!r})"
        )

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
            num_channels = (
                int(getattr(type(leaf.params), "num_channels", 0) or 0)
                if isinstance(getattr(leaf.params, "channels", None), dict)
                else 0
            )
            if num_channels > 1:
                if ch_idx is None:
                    raise ValueError(
                        f"Selection for {var_name} uses multi-channel instrument "
                        f"{leaf.type}:{leaf.key}; channel_index is required"
                    )
                if ch_idx < 0 or ch_idx >= num_channels:
                    raise ValueError(
                        f"Invalid channel_index {ch_idx} for {leaf.type}:{leaf.key}; "
                        f"valid range is 0..{num_channels - 1}"
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
    attribute_for: dict[str, str],
    instrument_reqs: list[FilledReq],
    saver_reqs: list[FilledReq],
    plotter_reqs: list[FilledReq],
    saver_selections: list[SelectedResource],
    plotter_selections: list[SelectedResource],
    template_text: str,
) -> str:
    """Generate setup using ``resources.from_attribute``.

    ``attribute_for`` maps each instrument variable to the ``attribute_name`` it
    resolves through, already computed by the caller — a routed instrument has no
    params in this workspace to read one from, so deriving it here is not
    possible. This is the only style that works for a multi-source project,
    because an attribute name is the one handle meaningful on both sides of the
    wire.

    Saver/plotter handling is the same as in ``_compose_setup``: they are keyed
    by user-given name, not by attribute_name, and always resolve locally.
    """
    if not instrument_reqs and not saver_reqs and not plotter_reqs:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")
    missing = [
        r.variable_name for r in instrument_reqs if not attribute_for.get(r.variable_name)
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
        attr_name = attribute_for[req.variable_name]
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


def _local_attribute_name(leaf: _NodeRef, channel_index: int | None) -> str:
    """``attribute_name`` of a local selection, or ``""`` if it has none."""
    if channel_index is not None:
        ch_map = getattr(leaf.params, "channels", None)
        if isinstance(ch_map, dict) and channel_index in ch_map:
            return getattr(ch_map[channel_index], "attribute_name", "") or ""
        return ""
    return getattr(leaf.params, "attribute_name", "") or ""


def _unnamed_local(
    local_nodes: dict[str, _NodeRef], attribute_for: dict[str, str]
) -> list[str]:
    """Local selections with no ``attribute_name``, as ``type:key`` labels."""
    return [
        f"{local_nodes[var].type}:{local_nodes[var].key}"
        for var, attr in attribute_for.items()
        if not attr and var in local_nodes
    ]


class _Resolved(NamedTuple):
    """Instrument selections split by where they come from."""

    local_nodes: dict[str, _NodeRef]
    local_channels: dict[str, int | None]
    attribute_for: dict[str, str]
    instrument_sources: dict[str, str]
    routed: bool


def _resolve_instrument_selections(
    config_dir: Path,
    selections: list[SelectedResource],
    all_nodes: list[_NodeRef],
) -> _Resolved:
    """Validate every instrument selection against the source that owns it.

    A local selection is resolved against this workspace's tree exactly as
    before. A routed one cannot be — there are no params here — so it is checked
    against the source's live answer instead. Re-asking costs one round trip and
    turns a picker that has been open while a daemon stopped into a clear message
    rather than a project that fails on first run.
    """
    local_nodes: dict[str, _NodeRef] = {}
    local_channels: dict[str, int | None] = {}
    attribute_for: dict[str, str] = {}
    source_of_var: dict[str, str] = {}

    offered: dict[str, dict[str, Any]] = {}
    registered: dict[str, str] = {}

    for sel in selections:
        if sel.source == LOCAL:
            leaf = _resolve_selection_node(sel, all_nodes)
            local_nodes[sel.variable_name] = leaf
            local_channels[sel.variable_name] = sel.channel_index
            attribute_for[sel.variable_name] = _local_attribute_name(
                leaf, sel.channel_index
            )
            source_of_var[sel.variable_name] = LOCAL
            continue

        if not sel.attribute:
            raise ValueError(
                f"Selection for '{sel.variable_name}' comes from source "
                f"{sel.source!r} but carries no attribute name. An instrument on "
                "another workspace or machine is referenced by attribute_name."
            )
        if sel.source not in offered:
            offered[sel.source] = {
                a.get("attribute_name"): a
                for a in attributes_for_source(config_dir, sel.source)
                if a.get("attribute_name")
            }
            # Record the name in the address book so the generated project can
            # resolve it at run time without anyone typing a URL.
            registered[sel.source] = ensure_source_registered(
                config_dir, sel.source, resolve_source_url(config_dir, sel.source)
            )
        if sel.attribute not in offered[sel.source]:
            raise ValueError(
                f"Source {sel.source!r} no longer offers an instrument named "
                f"{sel.attribute!r} (needed for '{sel.variable_name}'). Its config "
                "may have changed since this page was loaded — reload and pick again."
            )
        attribute_for[sel.variable_name] = sel.attribute
        source_of_var[sel.variable_name] = registered[sel.source]

    routed = any(source != LOCAL for source in source_of_var.values())

    # A routed project resolves *every* instrument by attribute name, including
    # its local ones, so a local instrument without one has to be named first.
    if routed:
        unnamed = _unnamed_local(local_nodes, attribute_for)
        if unnamed:
            raise ValueError(
                "This measurement mixes local and server instruments, so every "
                "instrument is referenced by attribute_name — but "
                f"{', '.join(unnamed)} has none. Set one in Manage Instruments "
                "and try again."
            )

    # Routing is keyed on attribute name, so one name cannot mean two different
    # instruments: the second entry would silently shadow the first.
    owner: dict[str, str] = {}
    for var, attr in attribute_for.items():
        if not attr:
            continue
        source = source_of_var[var]
        if attr in owner and owner[attr] != source:
            raise ValueError(
                f"Two sources both provide an instrument named {attr!r} "
                f"({owner[attr]} and {source}). A project routes instruments by "
                "attribute name, so one would shadow the other. Rename one of "
                "them before using both in a measurement."
            )
        owner[attr] = source

    return _Resolved(
        local_nodes=local_nodes,
        local_channels=local_channels,
        attribute_for=attribute_for,
        # Only meaningful when something is routed; a purely local project keeps
        # an empty mapping and so an unchanged YAML.
        instrument_sources=owner if routed else {},
        routed=routed,
    )


def _measurement_param_defaults(measurement_name: str) -> dict[str, Any]:
    """Default ``measurement.params`` for a measurement.

    Derived from the params model the measurement's own template declares, so
    adding a measurement needs no edit here. This used to be a dict keyed by
    measurement name while measurements themselves were discovered from the
    directory — meaning a new one generated a project with an empty params
    block and nothing said why.

    The model is constructed with no overrides and dumped to plain JSON-
    compatible values; ``model_validate`` on the same model round-trips it.
    """
    try:
        measurement = _measurement_info(measurement_name)
    except ValueError:
        # An unknown name is the caller's error to report, not this function's:
        # generate_measurement_project resolves requirements first and raises
        # there, so nothing reaches here with a bad name in the real flow.
        logger.info("No measurement named '%s'; no param defaults", measurement_name)
        return {}

    model = params_model_for_measurement(measurement)
    if model is None:
        # Legitimate for a measurement with no tunable parameters, so not an
        # error — but worth saying, since the alternative reading is a typo in
        # the template's params annotation.
        logger.info(
            "Measurement '%s' declares no params model; its project will have an "
            "empty measurement.params block",
            measurement_name,
        )
        return {}
    return model().model_dump(mode="json")


def _default_project_yaml(
    measurement_name: str,
    instruments: dict[str, Any],
    savers: dict[str, Any],
    plotters: dict[str, Any],
    instrument_sources: dict[str, str] | None = None,
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
            # Omitted entirely for a purely local project, so existing projects
            # and their YAML are unchanged. Present only when something is
            # routed, which is also what the setup file keys its behaviour off.
            **(
                {"instrument_sources": dict(instrument_sources)}
                if instrument_sources
                else {}
            ),
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

    resolved = _resolve_instrument_selections(config_dir, instrument_sels, all_nodes)
    inst_selected_map = resolved.local_nodes
    inst_selected_channels = resolved.local_channels

    if resolved.routed and req.generation_style != "from_attribute":
        # The other styles emit ``Cls.from_config(resources, key=<hash>)``, which
        # addresses a params tree this workspace does not have for a routed
        # instrument. attribute_name is the only handle that means the same thing
        # on both sides of the wire.
        logger.info(
            "Selection spans %d source(s); generating in from_attribute style "
            "instead of %s",
            len({*resolved.instrument_sources.values()}),
            req.generation_style,
        )
        req.generation_style = "from_attribute"

    if req.generation_style == "from_attribute" and not resolved.routed:
        # Chosen deliberately for an all-local project; same requirement, but the
        # user has not been told anything about sources, so say it plainly.
        unnamed = _unnamed_local(resolved.local_nodes, resolved.attribute_for)
        if unnamed:
            raise ValueError(
                "from_attribute generation requires attribute_name to be set on "
                f"{', '.join(unnamed)}. Set it in the instrument config and "
                "regenerate."
            )

    requirements = _requirements_for_measurement(req.measurement_name)
    instrument_reqs, saver_reqs, plotter_reqs = _split_requirements(requirements)
    template_text = _setup_template_text(req.measurement_name)
    measurement_source = _measurement_source_text(req.measurement_name)

    # Only local instruments contribute params. A routed one is owned by its
    # server; copying a snapshot here would create a second copy to drift.
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
        resolved.instrument_sources,
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
            resolved.attribute_for,
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
    measurement_path = project_dir / f"{req.measurement_name}.py"
    measurement_path.write_text(measurement_source, encoding="utf-8")
    logger.info(
        "Generated project artifacts yaml=%s setup=%s measurement=%s",
        yaml_path,
        setup_path,
        measurement_path,
    )

    return {
        "status": "ok",
        "project_dir": str(project_dir),
        "project_name": project_dir.name,
        "measurement_name": req.measurement_name,
        "yaml_file": str(yaml_path),
        "setup_file": str(setup_path),
        "measurement_file": str(measurement_path),
    }
