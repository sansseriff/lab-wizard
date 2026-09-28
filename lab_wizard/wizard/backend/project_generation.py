from __future__ import annotations

import logging
import re
from pathlib import Path
from textwrap import indent
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    model_to_commented_map,
    to_commented_yaml_value,
)
from lab_wizard.lib.utilities.model_tree import OutputsConfig
from lab_wizard.wizard.backend._generation_common import (
    BaseSelection,
    _build_subset_instruments_from_selected_nodes,
    _compose_pedagogical_embedded,
    _create_unique_project_dir,
    SelectedNodeRef,  # noqa: F401 - re-exported: selections name tree nodes by it
    _NodeRef,
    _resolve_selection_node,
    _sanitize_identifier,
    _walk_tree,
)
from lab_wizard.wizard.backend.instrument_sources import (
    LOCAL,
    attributes_for_source,
    ensure_source_registered,
    resolve_source_url,
)
from lab_wizard.wizard.backend.models import FilledReq
from lab_wizard.wizard.backend.python_formatting import format_python_code

logger = logging.getLogger("lab_wizard.wizard.backend.project_generation")


GenerationStyle = Literal["production", "pedagogical_embedded"]


class SelectedResource(BaseSelection):
    """The instrument picked for one role."""

    channel_index: int | None = None
    # Which source owns this instrument: ``local``, or a name from
    # /api/instrument-sources.
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
    # ``production`` names each instrument; ``pedagogical_embedded`` writes
    # every instrument's params into the file (plans/procedure_plan.md 5.10).
    generation_style: GenerationStyle = "production"
    # A named preset from config/measurements/<measurement>/, copied into the
    # project's measurement.params. None means the measurement's own defaults.
    params_preset: str | None = None
    # What ``measurement_name`` names: a procedure definition (config/procedures
    # or the built-in library), or a custom measurement in the workspace's
    # measurements folder (lib/custom_measurements.py).
    kind: Literal["procedure", "custom"] = "procedure"
    # What a run produces besides its database record, written to the project
    # YAML's outputs: block as given.
    outputs: OutputsConfig = Field(default_factory=OutputsConfig)


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


def _replace_optional_block(template_text: str, block_name: str, content: str) -> str:
    """Fill ``block_name`` if the template has it.

    A custom measurement's setup imports the measurement's own ``Resources``
    rather than declaring fields to fill, so it has no ``resource_fields``.
    """
    if f"# wizard:{block_name}:start" not in template_text:
        return template_text
    return _replace_wizard_block(template_text, block_name, content)


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
    template_text: str,
) -> str:
    """Setup code for the embedded teaching style."""
    if not instrument_reqs:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")

    missing = [
        r.variable_name
        for r in instrument_reqs
        if r.variable_name not in inst_selected_map
    ]
    if missing:
        raise ValueError(f"Missing required selections: {missing}")

    # Resource fields + return fields, in template field order
    resource_field_lines: list[str] = []
    return_field_lines: list[str] = []
    instrument_assignments: list[str] = []

    # Every param is written into the Python itself (the escape hatch in
    # plans/procedure_plan.md 5.10); production generation references
    # instruments by attribute_name instead and never reaches here.
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
    instrument_lines, imports, final_exprs = _compose_pedagogical_embedded(
        selections=selections,
        var_names=[req.variable_name for req in instrument_reqs],
        leaves=leaves,
    )
    for req, expr in zip(instrument_reqs, final_exprs):
        local_name = f"{req.variable_name}_1"
        instrument_assignments.append(f"{local_name} = {expr}")
        resource_field_lines.append(_format_resource_field_line(req))
        return_field_lines.append(_format_return_field_line(req, [local_name]))

    # Skip imports for symbols already in the template (e.g. base classes from
    # the wizard:resource_fields annotations).
    skip_names = _existing_import_symbols(template_text)
    filtered_imports = sorted(
        {f"from {mod} import {cls}" for mod, cls in imports if cls not in skip_names}
    )

    imports_block = "\n".join(filtered_imports)
    instantiation_lines = (
        instrument_lines
        + (["", ""] if instrument_assignments else [])
        + instrument_assignments
    )
    instantiation_block = "\n".join(instantiation_lines).rstrip()

    rendered = template_text
    rendered = _replace_wizard_block(rendered, "imports", imports_block)
    rendered = _replace_optional_block(
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
    template_text: str,
) -> str:
    """Generate setup using ``resources.from_attribute``.

    ``attribute_for`` maps each instrument variable to the ``attribute_name`` it
    resolves through, already computed by the caller — a routed instrument has no
    params in this workspace to read one from, so deriving it here is not
    possible. This is the only style that works for a multi-source project,
    because an attribute name is the one handle meaningful on both sides of the
    wire.
    """
    if not instrument_reqs:
        raise ValueError(f"No requirements found for measurement '{measurement_name}'")
    missing = [
        r.variable_name for r in instrument_reqs if not attribute_for.get(r.variable_name)
    ]
    if missing:
        raise ValueError(f"Missing required selections: {missing}")

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

    rendered = template_text
    rendered = _replace_wizard_block(rendered, "imports", "")
    rendered = _replace_optional_block(
        rendered, "resource_fields", "\n".join(resource_field_lines)
    )
    rendered = _replace_wizard_block(
        rendered, "instantiation", "\n".join(instrument_assignments)
    )
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
        # Every named instrument, local ones included: a project resolves each
        # attribute against the tree its source names (plans/procedure_plan.md 5.4).
        instrument_sources=owner,
        routed=routed,
    )


def commented_params(params: dict[str, Any], model: type[BaseModel] | None) -> Any:
    """``params`` for a project YAML: with each field's description as a comment,
    ``# (V) the bias voltages to visit``, when ``model`` accepts them."""
    if model is None:
        return params
    try:
        return model_to_commented_map(model.model_validate(params))
    except ValueError:
        return params


def _default_project_yaml(
    measurement_name: str,
    instruments: dict[str, Any],
    instrument_sources: dict[str, str] | None,
    *,
    params: dict[str, Any],
    outputs: OutputsConfig | None = None,
    kind: str = "procedure",
    params_model: type[BaseModel] | None = None,
) -> dict[str, Any]:
    return {
        "project": {
            "schema_version": 1,
            "measurement_type": measurement_name,
            "kind": kind,
            "created_by": "lab_wizard",
        },
        # Who and what the run is about, recorded with every run. ``device`` names
        # the device under test in the lab database; it is filled in before a run.
        "run": {"device": None, "operator": None, "notes": None, "metadata": {}},
        "measurement": {
            # Written with each param's unit and description as a comment, as
            # instrument configs are, when the params model is known.
            "params": commented_params(params, params_model),
        },
        # What a run produces besides its database record, read each time it
        # starts: files (laid out by the workspace's data.yaml), and a live
        # plot for a run started from a terminal (none | window | web).
        "outputs": (outputs or OutputsConfig()).model_dump(mode="json"),
        "resources": {
            # Present only for the embedded style (and in projects generated
            # before instrument params left the project); otherwise instruments
            # are resolved from the tree instrument_sources names.
            **(
                {
                    "instruments": {
                        key: model_to_commented_map(value, exclude_none=True)
                        for key, value in instruments.items()
                    }
                }
                if instruments
                else {}
            ),
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


def _refuse_embedded_through_server(style: str, selections: list[Any]) -> None:
    """The embedded style cannot use an instrument through a server.

    It writes each instrument's params into the file and builds the object
    locally — for hardware a server owns, the one thing that must not happen.
    Checked before anything else (the address book, attribute names), and
    refused rather than quietly generated in production style: a file that is
    not the self-contained one asked for is worse than no file.
    """
    if style != "pedagogical_embedded":
        return
    routed = [sel.variable_name for sel in selections if getattr(sel, "source", LOCAL) != LOCAL]
    if routed:
        raise ValueError(
            "The embedded-params style writes every instrument's settings into the "
            "file and opens it directly, so it cannot use an instrument through a "
            f"server — and {', '.join(routed)} {'is' if len(routed) == 1 else 'are'} "
            "chosen from one. Pick local instruments, or use the production style."
        )


def _params_for(
    config_dir: Path, measurement: str, preset: str | None, model: type | None
) -> dict[str, Any] | None:
    """A project's initial ``measurement.params``: a preset, or ``None`` for defaults."""
    if preset is None:
        return None
    from lab_wizard.lib.procedures.storage import load_preset

    return load_preset(config_dir, measurement, preset, model)


def generate_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateProjectRequest,
    requirements: list[FilledReq],
    template_text: str,
    measurement_source: str,
    params: dict[str, Any],
    params_model: type[BaseModel] | None = None,
) -> dict[str, Any]:
    """Write a project from a setup template and a measurement module.

    Shared by composed procedures and custom measurements, which differ only in
    where these pieces come from.
    """
    style = req.generation_style

    instrument_sels = req.selected_resources
    _refuse_embedded_through_server(style, instrument_sels)
    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    resolved = _resolve_instrument_selections(config_dir, instrument_sels, all_nodes)
    inst_selected_map = resolved.local_nodes
    inst_selected_channels = resolved.local_channels

    instrument_reqs = requirements

    if style == "production":
        unnamed = _unnamed_local(resolved.local_nodes, resolved.attribute_for)
        if unnamed:
            raise ValueError(
                "A project references its instruments by attribute_name, but "
                f"{', '.join(unnamed)} has none. Manage Instruments names every "
                "instrument it saves, so this config was probably edited by hand: "
                "set an attribute_name and try again."
            )
        # No instrument params in the project: they are read from the tree each
        # attribute's source names, when the project runs.
        instruments_subset: dict[str, Any] = {}
        instrument_sources = resolved.instrument_sources
    else:
        # The escape hatch keeps a full copy, so the project runs outside any
        # workspace — the reason to choose it.
        instruments_subset = _build_subset_instruments_from_selected_nodes(
            [
                (leaf, inst_selected_channels.get(variable_name))
                for variable_name, leaf in inst_selected_map.items()
            ]
        )
        instrument_sources = {}

    prefix = req.project_prefix or _format_measurement_slug(req.measurement_name)
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created project directory %s", project_dir)

    yaml_payload = _default_project_yaml(
        req.measurement_name,
        instruments_subset,
        instrument_sources,
        params=params,
        outputs=req.outputs,
        kind=req.kind,
        params_model=params_model,
    )
    yaml_path = project_dir / f"{project_dir.name}.yaml"
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    if style == "production":
        setup_code = _compose_setup_from_attribute(
            req.measurement_name,
            resolved.attribute_for,
            instrument_reqs,
            template_text,
        )
    else:
        setup_code = _compose_setup(
            req.measurement_name,
            inst_selected_map,
            inst_selected_channels,
            instrument_reqs,
            template_text,
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
