"""Turn a procedure definition into the measurement module a generated project runs.

A project keeps its procedure in the ``procedure:`` block of its YAML; that is
the one place it is edited. ``<name>_measurement.py`` is built from it and
built again whenever it changes (``lab_wizard.lib.project``), so it is never
edited by hand and never out of date. It holds:

* the params classes (typed fields, no defaults: the values are the project's,
  in ``measurement.params`` of the same YAML) and ``<Name>Resources``, a frozen
  dataclass with one field per role typed by the behavior it needs;
* ``build_<name>_procedure(resources)``, the step tree;
* the hash of the ``procedure:`` block it was built from, to notice a change.

The project's setup file (``wizard/backend/setup_generation.py``) subclasses
``<Name>Resources`` to narrow each role to the class of its instrument.

Output is valid but unformatted; format it with ``format_python_code``.
"""

from __future__ import annotations

import hashlib
import json
import pprint
import re
from typing import Any

from lab_wizard.lib.procedures.definition import ParamDecl, ParamTree, ProcedureDefinition, _SWEEP_ADAPTER
from lab_wizard.lib.procedures.spec import RenderContext, python_identifier


__all__ = [
    "built_from",
    "class_prefix",
    "definition_block",
    "measurement_module_source",
    "params_models_source",
    "procedure_block",
    "procedure_hash",
    "types_block",
]
_SWEEP_MODULE = "lab_wizard.lib.procedures.sweep_params"


def class_prefix(name: str) -> str:
    """``mcr_curve`` -> ``McrCurve``."""
    return "".join(part.capitalize() for part in python_identifier(name).split("_") if part) or "Procedure"


def _imports(pairs: set[tuple[str, str]]) -> str:
    by_module: dict[str, set[str]] = {}
    for module, name in pairs:
        by_module.setdefault(module, set()).add(name)
    return "\n".join(
        f"from {module} import {', '.join(sorted(names))}" for module, names in sorted(by_module.items())
    )


# --------------------------- params models ---------------------------


def _field_line(name: str, decl: ParamDecl, imports: set[tuple[str, str]]) -> str:
    """One param as a typed field. No default: its value is the project's, in its YAML."""
    description = decl.full_description()
    if decl.type == "sweep":
        # SweepParams is an Annotated alias, whose __module__ says "typing".
        imports.add((_SWEEP_MODULE, "SweepParams"))
        annotation = "SweepParams"
    else:
        annotation = decl.type
    return f"    {name}: {annotation} = Field(description={description!r})" if description else f"    {name}: {annotation}"


def params_models_source(
    tree: ParamTree,
    root_class: str,
    imports: set[tuple[str, str]],
    doc: str = "The params of one run. Their values are the project's, in measurement.params of its YAML.",
) -> str:
    """Pydantic classes for a param tree, innermost first, ``root_class`` last.

    They say what params there are and what type each is; no field has a
    default, so a value comes only from where the project keeps it.
    """
    blocks: list[str] = []

    def emit(group: ParamTree, class_name: str, doc: str) -> None:
        lines: list[str] = [f"class {class_name}(BaseModel):", f"    {doc!r}"]
        if not group.entries:
            lines.append("    pass")
        for name, entry in group.entries.items():
            if isinstance(entry, ParamTree):
                sub = f"{class_name[: -len('Params')]}{class_prefix(name)}Params"
                emit(entry, sub, f"The {name!r} params group.")
                lines.append(f"    {name}: {sub}")
            else:
                lines.append(_field_line(name, entry, imports))
        blocks.append("\n".join(lines))

    imports.add(("pydantic", "BaseModel"))
    imports.add(("pydantic", "Field"))
    emit(tree, root_class, doc)
    return "\n\n\n".join(blocks)


# --------------------------- measurement module ---------------------------


def procedure_block(definition: ProcedureDefinition) -> tuple[str, set[tuple[str, str]]]:
    """The ``return <step tree>`` statement between the procedure markers."""
    definition.check()
    expr, ctx = definition.render_body()
    return f"return {expr}", ctx.imports


def definition_block(definition: ProcedureDefinition) -> str:
    """``DEFINITION = {...}``: the definition as a Python literal.

    An embedded-style setup file carries its procedure this way, since it reads
    nothing from the project's YAML; every run records it.
    """
    data = pprint.pformat(definition.model_dump(mode="json"), width=88, sort_dicts=False)
    return f"DEFINITION: dict[str, Any] = {data}"


def types_block(definition: ProcedureDefinition, imports: set[tuple[str, str]]) -> str:
    """The params classes and the ``Resources`` dataclass, between the types markers."""
    prefix = class_prefix(definition.name)
    params = params_models_source(definition.param_tree, f"{prefix}Params", imports)
    behaviors = definition.role_behaviors()
    roles = "\n".join(f"    {role}: {cls.__name__}" for role, cls in behaviors.items())
    imports.update((cls.__module__, cls.__name__) for cls in behaviors.values())
    imports.add(("dataclasses", "dataclass"))
    return (
        f"{params}\n\n\n"
        "@dataclass(frozen=True)\n"
        f"class {prefix}Resources:\n"
        f'    """What ``{definition.name}`` needs: an instrument per role, by what it does, and its params.\n\n'
        "    A project's setup file narrows each role to the class of the instrument it\n"
        '    is bound to."""\n\n'
        + (f"{roles}\n" if roles else "")
        + f"    params: {prefix}Params"
    )


def procedure_hash(procedure: Any) -> str:
    """The fingerprint of a ``procedure:`` block, to tell whether a module was built from it."""
    return hashlib.sha256(json.dumps(procedure, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


_HASH_LINE = re.compile(r"^procedure sha256: ([0-9a-f]{64})$", re.M)


def built_from(module_text: str) -> str | None:
    """The ``procedure_hash`` a generated module records, or ``None`` if it has none."""
    match = _HASH_LINE.search(module_text)
    return match.group(1) if match else None


def measurement_module_source(definition: ProcedureDefinition, source: Any = None) -> str:
    """The whole measurement module for ``definition``.

    ``source`` is the ``procedure:`` block it is built from, as the project's
    YAML has it; its hash goes in the module, so a changed block is noticed.
    """
    prefix = class_prefix(definition.name)
    fn = f"build_{python_identifier(definition.name)}_procedure"
    block, step_imports = procedure_block(definition)
    imports = set(step_imports) | {("lab_procedure", "Step")}
    types = types_block(definition, imports)
    roles = "\n".join(f"    {python_identifier(r)} = resources.{r}" for r in definition.roles)
    description = definition.description or f"The {definition.name} procedure."
    fingerprint = procedure_hash(source if source is not None else definition.model_dump(mode="json"))
    return f'''"""{description}

Generated by lab_wizard from the ``procedure:`` block of this project's YAML,
and generated again from it whenever that block changes — before the next run,
or with ``wizard regenerate``. Do not edit this file: change the procedure in
the YAML. What a procedure cannot say belongs in a custom measurement.

procedure sha256: {fingerprint}
"""

from __future__ import annotations

{_imports(imports)}


{types}


def {fn}(resources: {prefix}Resources) -> Step:
    """Build the step tree for one run of ``{definition.name}``."""
    params = resources.params
{roles}
    {block}
'''
