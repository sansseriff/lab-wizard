"""A procedure definition: roles, params, and a step tree.

Stored as YAML under ``config/procedures/<name>.yml``::

    name: pcr_curve
    description: Count rate against bias voltage
    roles:
      voltage_source: {behavior: VSource}
      counter: {behavior: Counter}
    params:
      bias:
        sweep: {type: sweep, default: {mode: linear, start: 0.0, stop: 0.04, step: 0.001}}
        settle_s: {type: float, default: 0.05, unit: s}
      readout:
        gate_time_s: {type: float, default: 1.0, unit: s}
    body:
      type: sweep
      parameter: bias_voltage
      values: {param: bias.sweep}
      body:
        type: sequence
        children:
          - {type: set_voltage, source: {role: voltage_source}, voltage: {swept: bias_voltage}}
          - {type: wait, seconds: {param: bias.settle_s}}
          - {type: count, counter: {role: counter}, gate_time: {param: readout.gate_time_s}}

The **roles** are the procedure's signature: which behaviors it needs, not which
instruments. The **params** are a tree of groups and typed leaves — the same
shape as a measurement's params model, and turned into one. The **body** is the
step tree.
"""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from typing import Any, Iterator, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model, field_validator, model_validator

from lab_wizard.lib.data.expressions import (
    ExpressionError,
    compile_expression,
    expression_names,
    expression_params,
)
from lab_wizard.lib.data.plot import PlotSpec
from lab_wizard.lib.procedures.sweep_params import SweepParams
from lab_wizard.lib.procedures.spec import (
    AnyStep,
    ParamRef,
    ProcedureError,
    RenderContext,
    StepParams,
    StepPath,
    python_identifier,
)


__all__ = ["ParamDecl", "ParamTree", "ProcedureDefinition", "RoleDecl"]

ParamType = Literal["float", "int", "bool", "str", "sweep"]
_PY_TYPES: dict[str, type] = {"float": float, "int": int, "bool": bool, "str": str}
_SWEEP_ADAPTER: TypeAdapter[Any] = TypeAdapter(SweepParams)

# Names a role or param cannot take, because the generated code already uses them.
_RESERVED_ROLES = {"params", "resources", "project"}


def _check_name(name: str, what: str) -> None:
    if not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(f"{what} {name!r} must be a valid Python identifier")
    if name.startswith("model_") or name in dir(BaseModel):
        raise ValueError(f"{what} {name!r} collides with a pydantic attribute")


class ParamDecl(BaseModel):
    """One param: its type, default, and what it means."""

    model_config = ConfigDict(extra="forbid")

    type: ParamType
    default: Any = None
    description: str = ""
    unit: Optional[str] = None

    @model_validator(mode="after")
    def _default_matches_type(self) -> "ParamDecl":
        if self.type == "sweep":
            self.default = _SWEEP_ADAPTER.validate_python(
                self.default if self.default is not None else {"mode": "linear"}
            ).model_dump(mode="json")
        elif self.default is None:
            self.default = _PY_TYPES[self.type]()
        else:
            self.default = TypeAdapter(_PY_TYPES[self.type]).validate_python(self.default)
        return self

    def python_type(self) -> Any:
        return SweepParams if self.type == "sweep" else _PY_TYPES[self.type]

    def full_description(self) -> str:
        unit = f"({self.unit}) " if self.unit else ""
        return f"{unit}{self.description}".strip()


@dataclass
class ParamTree:
    """A group of params: each entry a :class:`ParamDecl` or a nested group."""

    entries: dict[str, "ParamDecl | ParamTree"]

    @classmethod
    def parse(cls, data: dict[str, Any], path: str = "") -> "ParamTree":
        entries: dict[str, ParamDecl | ParamTree] = {}
        for name, value in (data or {}).items():
            _check_name(name, f"Param {path}{name}")
            if not isinstance(value, dict):
                raise ValueError(f"Param {path}{name} must be a mapping, got {value!r}")
            if isinstance(value.get("type"), str):
                entries[name] = ParamDecl.model_validate(value)
            else:
                entries[name] = cls.parse(value, f"{path}{name}.")
        return cls(entries)

    def find(self, dotted: str) -> Optional[ParamDecl]:
        node: ParamDecl | ParamTree = self
        for part in dotted.split("."):
            if not isinstance(node, ParamTree) or part not in node.entries:
                return None
            node = node.entries[part]
        return node if isinstance(node, ParamDecl) else None

    def leaves(self, prefix: str = "") -> Iterator[tuple[str, ParamDecl]]:
        for name, entry in self.entries.items():
            if isinstance(entry, ParamDecl):
                yield f"{prefix}{name}", entry
            else:
                yield from entry.leaves(f"{prefix}{name}.")

    def model(self, class_name: str) -> type[BaseModel]:
        """A pydantic model for these params, built at run time.

        Used to validate presets and produce defaults without generating code.
        The generated setup file declares the same model as source.
        """
        fields: dict[str, Any] = {}
        for name, entry in self.entries.items():
            if isinstance(entry, ParamTree):
                sub = entry.model(f"{class_name}{name.title().replace('_', '')}")
                fields[name] = (sub, Field(default_factory=sub))
            elif entry.type == "sweep":
                default = entry.default
                fields[name] = (
                    SweepParams,
                    Field(
                        default_factory=lambda d=default: _SWEEP_ADAPTER.validate_python(d),
                        description=entry.full_description(),
                    ),
                )
            else:
                fields[name] = (entry.python_type(), Field(default=entry.default, description=entry.full_description()))
        return create_model(class_name, **fields)


class RoleDecl(BaseModel):
    """One role: the behavior an instrument must have to fill it."""

    model_config = ConfigDict(extra="forbid")

    behavior: str
    description: str = ""


class ProcedureDefinition(BaseModel):
    """A procedure as stored. :meth:`check` says whether it can be generated."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    name: str
    description: str = ""
    roles: dict[str, RoleDecl] = Field(default_factory=dict)
    params: dict[str, Any] = Field(default_factory=dict)
    body: AnyStep
    # What a run of this procedure is usually looked at as. The first is what
    # the Data page and a live plotter draw by default.
    plots: list[PlotSpec] = Field(default_factory=list)
    # Columns computed from the recorded ones when read, never stored:
    # {"above_dark": 'count_rate - mean(count_rate, phase == "background")'}.
    derived: dict[str, str] = Field(default_factory=dict)

    @field_validator("name")
    @classmethod
    def _name_is_an_identifier(cls, name: str) -> str:
        _check_name(name, "Procedure name")
        return name

    @field_validator("roles")
    @classmethod
    def _role_names(cls, roles: dict[str, RoleDecl]) -> dict[str, RoleDecl]:
        for name in roles:
            _check_name(name, "Role")
            if name in _RESERVED_ROLES:
                raise ValueError(f"Role {name!r} is reserved by the generated code")
        return roles

    @field_validator("params")
    @classmethod
    def _params_parse(cls, params: dict[str, Any]) -> dict[str, Any]:
        ParamTree.parse(params)  # raises on a malformed tree
        return params

    # ------------------------- views -------------------------

    @property
    def param_tree(self) -> ParamTree:
        return ParamTree.parse(self.params)

    def params_model(self, class_name: Optional[str] = None) -> type[BaseModel]:
        return self.param_tree.model(class_name or f"{python_identifier(self.name).title().replace('_', '')}Params")

    def param_defaults(self) -> dict[str, Any]:
        return self.params_model()().model_dump(mode="json")

    def role_behaviors(self) -> dict[str, type]:
        """``{role: behavior class}``. Raises if a behavior is not registered."""
        registered = _behaviors()
        missing = [d.behavior for d in self.roles.values() if d.behavior not in registered]
        if missing:
            raise ProcedureError(
                [f"Unknown behavior {b!r}; registered: {', '.join(sorted(registered))}" for b in missing]
            )
        return {role: registered[decl.behavior] for role, decl in self.roles.items()}

    def columns(self) -> dict[str, dict[str, Any]]:
        """``{name: {"unit": ...}}`` for every column this procedure's rows can carry.

        In tree order, like :meth:`emitted_fields`. A swept column takes the
        unit of the sweep param it iterates; a recorded field takes the unit its
        step declares.
        """
        tree = self.param_tree
        out: dict[str, dict[str, Any]] = {}
        for step in self.body.walk():
            values = getattr(step, "values", None)
            decl = tree.find(values.param) if isinstance(values, ParamRef) else None
            for name in step.swept_parameters():
                out.setdefault(name, {"unit": decl.unit if decl is not None else None})
            for name in self._sweep_also(step, tree):
                out.setdefault(name, {"unit": None})
            for name, unit in step.emitted_units().items():
                out.setdefault(name, {"unit": unit})
        return out

    def emitted_fields(self) -> list[str]:
        """Every column this procedure's rows can carry, in tree order."""
        tree = self.param_tree
        seen: dict[str, None] = {}
        for step in self.body.walk():
            for name in (*step.swept_parameters(), *self._sweep_also(step, tree), *step.emitted_fields()):
                seen.setdefault(name, None)
        return list(seen)

    @staticmethod
    def _sweep_also(step: Any, tree: ParamTree) -> list[str]:
        """What a sweep records beside its value, going by its param's default mode.

        A waypoints sweep records ``<parameter>_leg``. A run whose params pick
        another mode simply records no leg; a plot of it then draws one line.
        """
        values = getattr(step, "values", None)
        decl = tree.find(values.param) if isinstance(values, ParamRef) else None
        if decl is None or decl.type != "sweep":
            return []
        also = _SWEEP_ADAPTER.validate_python(decl.default).also(step.parameter)
        return list(also)

    # ------------------------- checking -------------------------

    def render_body(self, ctx: Optional[RenderContext] = None) -> tuple[str, RenderContext]:
        """The body as a Python expression, with whatever rendering found wrong."""
        ctx = ctx or RenderContext(
            roles={role: python_identifier(role) for role in self.roles},
            params=self.param_tree,
        )
        ctx.paths.update((id(step), path) for path, step in self.body.walk_paths())
        return ctx.step(self.body), ctx

    def diagnose(self) -> list[tuple[StepPath, str]]:
        """Every reason this cannot be generated, each with where it is."""
        located: list[tuple[StepPath, str]] = []
        registered = _behaviors()
        for role, decl in self.roles.items():
            if decl.behavior not in registered:
                located.append(
                    (("roles", role), f"Unknown behavior {decl.behavior!r}; registered: {', '.join(sorted(registered))}")
                )
        behaviors = {role: registered[d.behavior] for role, d in self.roles.items() if d.behavior in registered}

        _expr, ctx = self.render_body()
        located.extend(ctx.located)

        for role, where, requires, path in ctx.role_uses:
            declared = behaviors.get(role)
            if declared is None or not requires:
                continue
            allowed = [registered[b] for b in requires if b in registered]
            if not any(issubclass(declared, cls) for cls in allowed):
                located.append(
                    (
                        path,
                        f"{where} needs a {' or '.join(requires)}, but role {role!r} is a "
                        f"{self.roles[role].behavior}",
                    )
                )

        emitted = set(self.emitted_fields())
        for path, step in self.body.walk_paths():
            field = getattr(step, "field", None)
            if step.type in ("value_above", "value_below") and field not in emitted:
                located.append(
                    (
                        path,
                        f"{step.label()} reads {field!r}, which no step in this procedure records "
                        f"(recorded: {', '.join(sorted(emitted)) or 'nothing'})",
                    )
                )
        located.extend(self._binding_problems())
        located.extend(self._derived_problems())
        located.extend(self._plot_problems())
        return located

    def warnings(self) -> list[tuple[StepPath, str]]:
        """Things that generate and run, but probably not as intended."""
        out: list[tuple[StepPath, str]] = []
        for path, fields in _loop_bodies(self.body, ("body",)):
            for field, count in fields.items():
                if count > 1:
                    out.append((
                        path,
                        f"{count} steps record {field!r} at the same parameter values, so each "
                        "reading lands on its own row. Give them different names, or put them "
                        "in a repeat if they are repetitions.",
                    ))
        return out

    # ------------------------- the data a run produces -------------------------

    def _binding_problems(self) -> list[tuple[StepPath, str]]:
        """A name bound twice, or recorded while bound, silently loses data."""
        out: list[tuple[StepPath, str]] = []

        def visit(step: StepParams, path: StepPath, bound: dict[str, StepPath]) -> None:
            for name in step.emitted_fields():
                if name in bound:
                    out.append((path, (
                        f"{step.label()} records {name!r}, which is a parameter bound by an enclosing "
                        f"step; every row already carries it. Record the reading under another name."
                    )))
            inner = dict(bound)
            for name in step.swept_parameters():
                if name in bound:
                    out.append((path, (
                        f"{step.label()} binds {name!r}, which an enclosing step already binds; the inner "
                        "value would replace the outer one in every row. Give it another name."
                    )))
                inner[name] = path
            for child_path, child in _children(step, path):
                visit(child, child_path, inner)

        visit(self.body, ("body",), {})
        return out

    def _data_columns(self) -> list[str]:
        return ["run_id", "seq", *self.emitted_fields()]

    def _derived_problems(self) -> list[tuple[StepPath, str]]:
        out: list[tuple[StepPath, str]] = []
        recorded = set(self._data_columns())
        for name in self.derived:
            if not name.isidentifier():
                out.append((("derived", name), f"derived column {name!r} must be a name, like above_dark"))
            elif name in recorded:
                out.append((("derived", name), f"derived column {name!r} would replace a recorded column"))
        # Dependency order, as ``derive`` computes them; a cycle never resolves.
        pending = dict(self.derived)
        while pending:
            ready = [n for n, text in pending.items() if not _names(text) & set(pending)]
            if not ready:
                for name in sorted(pending):
                    out.append((("derived", name), f"derived column {name!r} is part of a cycle"))
                break
            for name in ready:
                del pending[name]
        for name, text in self.derived.items():
            out.extend((("derived", name), m) for m in self._expression_problems(text, [*recorded, *self.derived]))
        return out

    def _plot_problems(self) -> list[tuple[StepPath, str]]:
        out: list[tuple[StepPath, str]] = []
        columns = [*self._data_columns(), *self.derived]
        for index, plot in enumerate(self.plots):
            where: StepPath = ("plots", index)
            if plot.runs:
                out.append((where, "a procedure's plot applies to every run of it, so it names no runs"))
            known = [*columns, *plot.derived]
            for key, text in plot.derived.items():
                out.extend(((*where, "derived", key), m) for m in self._expression_problems(text, known))
            expressions = [("x", plot.x), *(("y", y) for y in plot.y), *(("y2", y) for y in plot.y2)]
            expressions += [("where", key) for key in plot.where]
            if plot.series not in (None, "run"):
                expressions.append(("series", plot.series))
            for field, text in expressions:
                out.extend(((*where, field), m) for m in self._expression_problems(text, known))
        return out

    def _expression_problems(self, text: str, columns: list[str]) -> list[str]:
        problems = []
        tree = self.param_tree
        for path in sorted(_params_of(text)):
            decl = tree.find(path)
            if decl is None:
                problems.append(f"{text!r} reads param({path!r}), which is not declared")
            elif decl.type not in ("float", "int"):
                problems.append(f"{text!r} reads param({path!r}), which is a {decl.type}, not a number")
        try:
            compile_expression(text, columns, {0: self.param_defaults()})
        except ExpressionError as exc:
            if not problems:
                problems.append(str(exc))
        return problems

    def check(self) -> None:
        """Raise :class:`ProcedureError` listing every reason this cannot be generated."""
        located = self.diagnose()
        if located:
            raise ProcedureError([message for _path, message in located], located)


def _names(text: str) -> set[str]:
    try:
        return expression_names(text)
    except ExpressionError:
        return set()


def _params_of(text: str) -> set[str]:
    try:
        return expression_params(text)
    except ExpressionError:
        return set()


def _children(step: StepParams, path: StepPath) -> Iterator[tuple[StepPath, StepParams]]:
    for name in type(step).model_fields:
        value = getattr(step, name)
        if isinstance(value, StepParams):
            yield (*path, name), value
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, StepParams):
                    yield (*path, name, index), item


_LOOPS = ("sweep", "repeat")
# Only one of these children records in a given run of the step.
_ALTERNATIVES = {"if": ("then", "otherwise"), "selector": ("children",)}


def _field_counts(step: StepParams, path: StepPath, bodies: list[tuple[StepPath, dict[str, int]]]) -> dict[str, int]:
    """How many steps record each field, in one run of ``step`` (loops start afresh)."""
    if step.type in _LOOPS:
        bodies.append((path, _body_counts(step, path, bodies)))
        return {}
    counts: dict[str, int] = {}
    for name in step.emitted_fields():
        counts[name] = counts.get(name, 0) + 1
    alternatives = _ALTERNATIVES.get(step.type, ())
    branch_max: dict[str, int] = {}
    for child_path, child in _children(step, path):
        child_counts = _field_counts(child, child_path, bodies)
        target = branch_max if child_path[len(path)] in alternatives else None
        for name, n in child_counts.items():
            if target is not None:
                target[name] = max(target.get(name, 0), n)
            else:
                counts[name] = counts.get(name, 0) + n
    for name, n in branch_max.items():
        counts[name] = counts.get(name, 0) + n
    return counts


def _body_counts(loop: StepParams, path: StepPath, bodies: list[tuple[StepPath, dict[str, int]]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for child_path, child in _children(loop, path):
        for name, n in _field_counts(child, child_path, bodies).items():
            counts[name] = counts.get(name, 0) + n
    return counts


def _loop_bodies(body: StepParams, path: StepPath) -> list[tuple[StepPath, dict[str, int]]]:
    """``(path, {field: steps recording it})`` for the top level and every loop body."""
    bodies: list[tuple[StepPath, dict[str, int]]] = []
    top = _field_counts(body, path, bodies)
    return [(path, top), *bodies]


def _behaviors() -> dict[str, type]:
    # Importing the behavior modules registers them; the proxy registry imports
    # every shipped behavior.
    import lab_wizard.lib.client.proxies.registry  # noqa: F401
    from lab_wizard.lib.instruments.general.behavior import behaviors

    return dict(behaviors())
