"""Step schemas: the params model behind every procedure step.

Each runtime step (``Sweep``, ``SetVoltage``, ``Count``, …) has a
``*StepParams`` pydantic model, exactly as each instrument has a ``*Params``
model: a ``type`` literal discriminates it, ``step_class()`` names the runtime
class the way ``resource_class()`` does, and the instrument catalog discovers it
from ``lib/procedures/steps/``.

**No step needs its own generator.** A spec's field names are the runtime
constructor's parameter names, so :meth:`StepParams.render` reads the
constructor's signature and renders each argument from the field of the same
name. Adding a step is one small class; the generator never grows a case for it.
Only a step whose constructor takes something a field cannot hold — ``Sweep``'s
``child_factory`` closure — overrides ``render``.

A field holds one of:

* another step, or a list of steps — rendered recursively
* a :class:`RoleRef` — the instrument bound to that role
* a value: a literal, a :class:`ParamRef` into the procedure's params, or a
  :class:`SweptRef` to the value an enclosing ``Sweep`` is currently at
"""

from __future__ import annotations

import inspect
import keyword
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, SerializeAsAny

from lab_procedure import Step

if TYPE_CHECKING:
    from lab_wizard.lib.procedures.definition import ParamTree


__all__ = [
    "AnyStep",
    "StepClass",
    "StepPath",
    "ParamRef",
    "ProcedureError",
    "RenderContext",
    "Requires",
    "RoleRef",
    "StepParams",
    "SweptRef",
    "Value",
]


# Spelled once at module level: inside a step schema, ``type`` names the
# discriminator field, so ``-> type[Step]`` there would not mean the builtin.
StepClass = type[Step]


StepPath = tuple[str | int, ...]
"""Where in a definition something is: ``("body", "children", 2, "body")``.

The same shape as a pydantic error's ``loc``, so a problem found by validation
and one found by checking point at a step the same way.
"""


class ProcedureError(ValueError):
    """A procedure definition that cannot be generated, with every reason why.

    ``located`` pairs each problem with the path of the step (or role) it is
    about, for an editor to mark; ``()`` when it is about the whole definition.
    """

    def __init__(self, problems: list[str], located: list[tuple[StepPath, str]] | None = None) -> None:
        self.problems = list(problems)
        self.located = list(located) if located is not None else [((), p) for p in self.problems]
        super().__init__("; ".join(self.problems))


# --------------------------- references ---------------------------


class RoleRef(BaseModel):
    """The instrument bound to one of the procedure's roles: ``{role: counter}``."""

    model_config = ConfigDict(extra="forbid")
    role: str


class ParamRef(BaseModel):
    """A value from the procedure's params: ``{param: readout.gate_time_s}``."""

    model_config = ConfigDict(extra="forbid")
    param: str


class SweptRef(BaseModel):
    """The value an enclosing sweep is at: ``{swept: bias_voltage}``."""

    model_config = ConfigDict(extra="forbid")
    swept: str


# Order matters to pydantic only for ambiguity; a mapping can only be one of the
# three references, and a bare scalar is its own literal.
Value = Union[ParamRef, SweptRef, bool, int, float, str, list[float]]


@dataclass(frozen=True)
class Requires:
    """Field metadata: this role must be bound to one of these behaviors.

        source: Annotated[RoleRef, Requires("VSource")]
    """

    behaviors: tuple[str, ...]

    def __init__(self, *behaviors: str) -> None:
        object.__setattr__(self, "behaviors", tuple(behaviors))


# --------------------------- parsing nested steps ---------------------------


def _parse_step(value: Any) -> Any:
    """Build the right ``*StepParams`` subclass for a nested step mapping."""
    if value is None or isinstance(value, StepParams):
        return value
    if isinstance(value, dict):
        type_str = value.get("type")
        if not isinstance(type_str, str):
            raise ValueError(f"A step needs a 'type'; got keys {sorted(value)}")
        from lab_wizard.lib.procedures.catalog import step_params_class

        return step_params_class(type_str).model_validate(value)
    return value


# --------------------------- rendering ---------------------------


_IDENTIFIER = re.compile(r"[^0-9a-zA-Z_]")


def python_identifier(name: str) -> str:
    """A valid, non-keyword Python identifier derived from ``name``."""
    ident = _IDENTIFIER.sub("_", name).strip("_") or "value"
    if ident[0].isdigit():
        ident = f"_{ident}"
    if keyword.iskeyword(ident):
        ident = f"{ident}_"
    return ident


@dataclass
class RenderContext:
    """What a step needs to render itself, and what rendering discovers.

    Rendering doubles as validation: a reference to a role that does not exist,
    a param that is not declared, or a sweep value outside its sweep is
    collected in ``problems`` instead of producing code that fails at run time.
    """

    roles: dict[str, str]
    params: "ParamTree | None" = None
    params_var: str = "params"
    swept: dict[str, str] = field(default_factory=dict)
    imports: set[tuple[str, str]] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    # (role, where, required behaviors, path of the step using it)
    role_uses: list[tuple[str, str, tuple[str, ...], StepPath]] = field(default_factory=list)
    located: list[tuple[StepPath, str]] = field(default_factory=list)
    # Where each step sits in the definition, by identity, and the path of the
    # step being rendered now — shared by every scoped child context.
    paths: dict[int, StepPath] = field(default_factory=dict)
    path_stack: list[StepPath] = field(default_factory=list)

    def scoped(self, parameter: str) -> tuple["RenderContext", str]:
        """A child context in which ``parameter`` is a swept value."""
        taken = set(self.swept.values()) | set(self.roles.values()) | {self.params_var}
        base = python_identifier(parameter)
        ident, n = base, 2
        while ident in taken:
            ident, n = f"{base}_{n}", n + 1
        child = RenderContext(
            roles=self.roles,
            params=self.params,
            params_var=self.params_var,
            swept={**self.swept, parameter: ident},
            imports=self.imports,
            problems=self.problems,
            role_uses=self.role_uses,
            located=self.located,
            paths=self.paths,
            path_stack=self.path_stack,
        )
        return child, ident

    @property
    def path(self) -> StepPath:
        return self.path_stack[-1] if self.path_stack else ()

    def problem(self, message: str) -> None:
        """Record a reason the tree cannot be generated, at the step being rendered."""
        self.problems.append(message)
        self.located.append((self.path, message))

    def use(self, cls: type) -> str:
        self.imports.add((cls.__module__, cls.__name__))
        return cls.__name__

    def step(self, spec: "StepParams") -> str:
        self.path_stack.append(self.paths.get(id(spec), self.path))
        try:
            return spec.render(self)
        finally:
            self.path_stack.pop()

    def role(self, ref: RoleRef, where: str, requires: tuple[str, ...]) -> str:
        if ref.role not in self.roles:
            self.problem(f"{where} uses role {ref.role!r}, which the procedure does not declare")
            return python_identifier(ref.role)
        self.role_uses.append((ref.role, where, requires, self.path))
        return self.roles[ref.role]

    def value(self, value: Any, where: str) -> str:
        if isinstance(value, ParamRef):
            if self.params is not None:
                decl = self.params.find(value.param)
                if decl is None:
                    self.problem(f"{where} reads param {value.param!r}, which is not declared")
                elif decl.type == "sweep":
                    self.problem(
                        f"{where} reads sweep param {value.param!r} as a single value; "
                        "only a Sweep's values can be a sweep"
                    )
            return f"{self.params_var}.{value.param}"
        if isinstance(value, SweptRef):
            if value.swept not in self.swept:
                self.problem(
                    f"{where} reads swept value {value.swept!r} outside any Sweep over it"
                )
                return python_identifier(value.swept)
            return self.swept[value.swept]
        if isinstance(value, dict):
            items = ", ".join(f"{k!r}: {self.value(v, f'{where}[{k!r}]')}" for k, v in value.items())
            return "{" + items + "}"
        return repr(value)


# --------------------------- the base ---------------------------


class StepParams(BaseModel):
    """Base for every step schema. Subclasses set ``type`` and ``step_class``."""

    model_config = ConfigDict(extra="forbid")

    type: str
    name: str | None = None

    # Data fields this step records, for plotting and for checking conditions.
    emits: ClassVar[tuple[str, ...]] = ()
    # The unit of each recorded field that has one: {"count_rate": "Hz"}.
    units: ClassVar[dict[str, str]] = {}

    @classmethod
    def step_class(cls) -> StepClass:
        raise NotImplementedError(f"{cls.__name__} does not name its runtime step")

    # ------------------------- structure -------------------------

    def child_steps(self) -> list["StepParams"]:
        """Direct child steps, in field order."""
        out: list[StepParams] = []
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, StepParams):
                out.append(value)
            elif isinstance(value, list):
                out.extend(v for v in value if isinstance(v, StepParams))
        return out

    def walk(self) -> Iterator["StepParams"]:
        """This step, then every descendant, depth first."""
        yield self
        for child in self.child_steps():
            yield from child.walk()

    def walk_paths(self, path: StepPath = ("body",)) -> Iterator[tuple[StepPath, "StepParams"]]:
        """Like :meth:`walk`, with each step's path in the definition."""
        yield path, self
        for name in type(self).model_fields:
            value = getattr(self, name)
            if isinstance(value, StepParams):
                yield from value.walk_paths((*path, name))
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, StepParams):
                        yield from item.walk_paths((*path, name, index))

    @classmethod
    def role_requirements(cls) -> dict[str, tuple[str, ...]]:
        """``{field: behaviors}`` for every role field."""
        out: dict[str, tuple[str, ...]] = {}
        for name, info in cls.model_fields.items():
            for meta in info.metadata:
                if isinstance(meta, Requires):
                    out[name] = meta.behaviors
        return out

    def emitted_fields(self) -> tuple[str, ...]:
        return self.emits

    def emitted_units(self) -> dict[str, str | None]:
        """``{field: unit}`` for every field this step records."""
        return {name: self.units.get(name) for name in self.emitted_fields()}

    def swept_parameters(self) -> tuple[str, ...]:
        """Names this step binds for its descendants (a Sweep binds one)."""
        return ()

    # ------------------------- rendering -------------------------

    def label(self) -> str:
        return f"{self.type}{f' {self.name!r}' if self.name else ''}"

    def render(self, ctx: RenderContext) -> str:
        """``StepClass(arg=…, …, name=…)``, argument by argument from the signature."""
        cls = self.step_class()
        class_name = ctx.use(cls)
        fields = type(self).model_fields
        requirements = self.role_requirements()
        parameters = list(inspect.signature(cls.__init__).parameters.values())[1:]
        has_varargs = any(p.kind is inspect.Parameter.VAR_POSITIONAL for p in parameters)

        positional: list[str] = []
        keywords: list[str] = []
        seen_varargs = False
        for p in parameters:
            if p.name == "name" or p.kind is inspect.Parameter.VAR_KEYWORD:
                continue
            if p.kind is inspect.Parameter.VAR_POSITIONAL:
                seen_varargs = True
                items = getattr(self, p.name, None) or []
                positional.extend(ctx.step(item) for item in items)
                continue
            if p.name not in fields:
                if p.default is inspect.Parameter.empty:
                    ctx.problem(
                        f"{type(self).__name__} has no field for {cls.__name__}'s "
                        f"required argument {p.name!r}"
                    )
                continue
            raw = getattr(self, p.name)
            if raw is None and p.default is None:
                continue
            rendered = self._render_field(ctx, p.name, raw, requirements)
            if has_varargs and not seen_varargs:
                positional.append(rendered)
            else:
                keywords.append(f"{p.name}={rendered}")
        if self.name:
            keywords.append(f"name={self.name!r}")
        return f"{class_name}({', '.join([*positional, *keywords])})"

    def _render_field(
        self, ctx: RenderContext, name: str, raw: Any, requirements: dict[str, tuple[str, ...]]
    ) -> str:
        where = f"{self.label()}.{name}"
        if isinstance(raw, StepParams):
            return ctx.step(raw)
        if isinstance(raw, list) and raw and all(isinstance(v, StepParams) for v in raw):
            return "[" + ", ".join(ctx.step(v) for v in raw) + "]"
        if isinstance(raw, RoleRef):
            return ctx.role(raw, where, requirements.get(name, ()))
        return ctx.value(raw, where)


AnyStep = Annotated[SerializeAsAny[StepParams], BeforeValidator(_parse_step)]
"""A field holding any registered step, parsed to its own ``*StepParams`` class."""
