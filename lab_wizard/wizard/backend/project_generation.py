from __future__ import annotations

import logging
import re
from pathlib import Path
from textwrap import indent
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field
from ruamel.yaml import YAML

from lab_wizard.lib.client.proxies.registry import proxy_class_for
from lab_wizard.lib.utilities.config_io import (
    load_instruments,
    model_to_commented_map,
    to_commented_yaml_value,
)
from lab_wizard.lib.custom_measurements import CustomMeasurement
from lab_wizard.lib.procedures.codegen import measurement_module_source
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.project_module import module_path
from lab_wizard.lib.task_adapters.run import database_path
from lab_wizard.lib.utilities.model_tree import OutputsConfig, RoleBinding
from lab_wizard.wizard.backend._generation_common import (
    BaseSelection,
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
from lab_wizard.wizard.backend.embedded_generation import Binding, embedded_setup_source
from lab_wizard.wizard.backend.setup_generation import RoleType, production_setup_source
from lab_wizard.wizard.backend.models import FilledReq
from lab_wizard.lib.utilities.python_formatting import format_python_code
from lab_wizard.lib.procedures.storage import load_preset

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


def _roles(requirements: list[FilledReq], resolved: "_Resolved") -> tuple[dict[str, Any], list[RoleType]]:
    """The YAML's ``roles:``, and the class the setup declares for each role.

    A local instrument is declared as its driver class (a channel's, for a
    channel); one reached through a server as the proxy for what it does, since
    that is the object the run is handed, with the server's own class noted.
    """
    roles: dict[str, Any] = {}
    types: list[RoleType] = []
    for req in requirements:
        var = req.variable_name
        attribute = resolved.attribute_for.get(var)
        if not attribute:
            raise ValueError(f"Missing required selections: [{var!r}]")
        note = ""
        if var in resolved.remote:
            server, offered = resolved.remote[var]
            cls: type = proxy_class_for(offered.get("behavior_abc"))
            hint = offered.get("type_hint")
            note = f"through {server}, where it is a {hint}" if hint else f"through {server}"
            binding = RoleBinding(instrument=attribute, server=server)
        else:
            leaf = resolved.local_nodes[var]
            cls = type(leaf.params).resource_class()
            if resolved.local_channels.get(var) is not None:
                cls = cls.channel_class  # type: ignore[attr-defined]
            binding = RoleBinding(instrument=attribute)
        roles[var] = [binding.as_yaml()] if req.is_list else binding.as_yaml()
        types.append(RoleType(role=var, module=cls.__module__, name=cls.__name__, is_list=req.is_list, note=note))
    return roles, types


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
    # For each variable reached through a server: the source's name and what it
    # reported about the instrument (behavior_abc, type_hint, ...).
    remote: dict[str, tuple[str, dict[str, Any]]]


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
    remote: dict[str, tuple[str, dict[str, Any]]] = {}

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
        remote[sel.variable_name] = (registered[sel.source], offered[sel.source][sel.attribute])

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
        remote=remote,
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


def _project_info(measurement_name: str, kind: str, *, style: str = "production") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "measurement_type": measurement_name,
        "kind": kind,
        "style": style,
        "created_by": "lab_wizard",
    }


def _default_project_yaml(
    measurement_name: str,
    roles: dict[str, Any],
    *,
    params: dict[str, Any],
    outputs: OutputsConfig | None = None,
    kind: str = "procedure",
    params_model: type[BaseModel] | None = None,
    procedure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "project": _project_info(measurement_name, kind),
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
        # Which instrument fills each role: its name in this workspace's
        # config/instruments, or {instrument, server} for one on a server. The
        # setup file says what class each is.
        "roles": roles,
    }
    if procedure is None:
        return payload
    # The procedure, in the file that holds its params' values: edit it here, and
    # <name>_measurement.py is built again from it before the next run.
    ordered = {key: payload[key] for key in ("project", "run", "roles")}
    return {**ordered, "procedure": procedure, **{k: v for k, v in payload.items() if k not in ordered}}


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

    return load_preset(config_dir, measurement, preset, model)


def generate_project(
    *,
    config_dir: Path,
    projects_dir: Path,
    req: GenerateProjectRequest,
    requirements: list[FilledReq],
    params: dict[str, Any],
    params_model: type[BaseModel] | None,
    measurement: ProcedureDefinition | CustomMeasurement,
) -> dict[str, Any]:
    """Write a project: its YAML, its setup file, and its measurement module.

    Shared by composed procedures and custom measurements. A production setup
    declares the class behind each role and reads the rest from the YAML
    (``setup_generation``); an embedded one says everything itself
    (``embedded_generation``).
    """
    style = req.generation_style

    instrument_sels = req.selected_resources
    _refuse_embedded_through_server(style, instrument_sels)
    instruments = load_instruments(config_dir)
    all_nodes = _walk_tree(instruments)

    resolved = _resolve_instrument_selections(config_dir, instrument_sels, all_nodes)

    if style == "production":
        unnamed = _unnamed_local(resolved.local_nodes, resolved.attribute_for)
        if unnamed:
            raise ValueError(
                "A project references its instruments by attribute_name, but "
                f"{', '.join(unnamed)} has none. Manage Instruments names every "
                "instrument it saves, so this config was probably edited by hand: "
                "set an attribute_name and try again."
            )

    prefix = req.project_prefix or _format_measurement_slug(req.measurement_name)
    project_dir = _create_unique_project_dir(projects_dir, prefix)
    logger.info("Created project directory %s", project_dir)

    # A procedure's definition, as the project's YAML carries it; the module is
    # built from exactly this, so its recorded hash matches from the start.
    procedure = (
        measurement.model_dump(mode="json", exclude_none=True) if isinstance(measurement, ProcedureDefinition) else None
    )
    if procedure is not None:
        measurement_source = format_python_code(measurement_module_source(measurement, procedure))
    else:
        # A custom measurement's module is its own file, copied as it is.
        measurement_source = measurement.path.read_text(encoding="utf-8")

    if style == "production":
        roles, role_types = _roles(requirements, resolved)
        yaml_payload = _default_project_yaml(
            req.measurement_name,
            roles,
            params=params,
            outputs=req.outputs,
            kind=req.kind,
            params_model=params_model,
            procedure=procedure,
        )
        custom = isinstance(measurement, CustomMeasurement)
        setup_code = production_setup_source(
            measurement_name=req.measurement_name,
            kind="custom" if custom else "procedure",
            roles=role_types,
            entry=measurement.entry if custom else "build_procedure",
            has_plots=bool(measurement.plots) if custom else False,
        )
    else:
        # Everything is in the setup file; the YAML only says what the project is.
        yaml_payload = {"project": _project_info(req.measurement_name, req.kind, style="embedded")}
        is_list = {r.variable_name: r.is_list for r in requirements}
        missing = [r.variable_name for r in requirements if r.variable_name not in resolved.local_nodes]
        if missing:
            raise ValueError(f"Missing required selections: {missing}")
        setup_code = embedded_setup_source(
            measurement=measurement,
            bindings=[
                Binding(
                    role=r.variable_name,
                    leaf=resolved.local_nodes[r.variable_name],
                    channel_index=resolved.local_channels.get(r.variable_name),
                    is_list=is_list.get(r.variable_name, False),
                )
                for r in requirements
            ],
            params=params,
            outputs=req.outputs,
            database=database_path(project_dir),
        )

    yaml_path = project_dir / f"{project_dir.name}.yaml"
    y = YAML(typ="rt")
    y.default_flow_style = False
    y_writer: Any = y
    with yaml_path.open("w", encoding="utf-8") as f:
        y_writer.dump(to_commented_yaml_value(yaml_payload), f)

    setup_path = project_dir / f"{req.measurement_name}_setup.py"
    setup_code = format_python_code(setup_code)
    setup_path.write_text(setup_code, encoding="utf-8")
    measurement_path = module_path(project_dir, req.measurement_name)
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
