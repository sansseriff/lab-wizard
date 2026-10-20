"""lab_wizard's procedure definition: lab_procedure's, plus how a run is looked at.

A procedure is stored as YAML under ``config/procedures/<name>.yml`` (see
:mod:`lab_procedure.definition` for the roles, params and body). lab_wizard
adds what its data layer reads:

* ``plots`` — what a run of this procedure is usually looked at as;
* ``derived`` — columns computed from the recorded ones when read;
* ``needs`` — facts about the setup its derived columns read, such as the bias
  resistor a current is inferred through. A measurement binds each to a field
  of its setup (``dev/plans/setup_plan.md``), and ``setup("name")`` reads it;

and checks roles against the registered instrument behaviors.

In a project's YAML the ``procedure:`` block is also where its params' values
live: each param's ``default`` is the value the project's runs use
(:func:`set_param_values`, :func:`carry_param_values`).
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lab_procedure import ProcedureDefinition as BaseProcedureDefinition
from lab_procedure import ProcedureError
from lab_procedure.definition import ParamTree
from lab_procedure.schema import StepPath

from lab_wizard.lib.data.expressions import (
    ExpressionError,
    compile_expression,
    expression_names,
    expression_params,
    expression_setup,
)
from lab_wizard.lib.data.plot import PlotSpec


__all__ = ["NeedDecl", "ProcedureDefinition", "carry_param_values", "set_param_values"]

# Names a role cannot take, because the generated code already uses them.
_RESERVED_ROLES = {"resources", "project"}


class NeedDecl(BaseModel):
    """A fact about the setup a procedure needs: ``bias_resistance: {unit: ohm}``.

    It is read in ``unit``, from whichever setup field a measurement binds it
    to; a field in kΩ reads as ohms. A need has no default: an unbound need
    stops a run before it starts, rather than scaling its data wrongly.
    """

    model_config = ConfigDict(extra="forbid")

    unit: str | None = None
    description: str = ""


class ProcedureDefinition(BaseProcedureDefinition):
    """A procedure as lab_wizard stores it. :meth:`check` says whether it can be generated."""

    # What a run of this procedure is usually looked at as. The first is what
    # the Data page and a live plotter draw by default.
    plots: list[PlotSpec] = Field(default_factory=list)
    # Columns computed from the recorded ones when read, never stored:
    # {"above_dark": 'count_rate - mean(count_rate, phase == "background")'}.
    derived: dict[str, str] = Field(default_factory=dict)
    # Facts about the setup the derived columns read with setup("name").
    needs: dict[str, NeedDecl] = Field(default_factory=dict)

    @field_validator("needs")
    @classmethod
    def _need_names(cls, needs: dict) -> dict:
        for name in needs:
            if not name.isidentifier():
                raise ValueError(f"Need {name!r} must be a name, like bias_resistance")
        return needs

    @field_validator("roles")
    @classmethod
    def _roles_not_reserved(cls, roles: dict) -> dict:
        for name in roles:
            if name in _RESERVED_ROLES:
                raise ValueError(f"Role {name!r} is reserved by the generated code")
        return roles

    def to_yaml(self) -> dict[str, Any]:
        """This procedure as a file or a project's ``procedure:`` block holds it.

        What was written, and nothing more: a field left out of the source is
        left out here, and takes its default from the code wherever it is read.
        """
        return self.model_dump(mode="json", exclude_unset=True)

    def role_behaviors(self) -> dict[str, type]:
        """``{role: behavior class}``. Raises if a behavior is not registered."""
        registered = _behaviors()
        missing = [d.behavior for d in self.roles.values() if d.behavior not in registered]
        if missing:
            raise ProcedureError(
                [f"Unknown behavior {b!r}; registered: {', '.join(sorted(registered))}" for b in missing]
            )
        return {role: registered[decl.behavior] for role, decl in self.roles.items()}

    def diagnose(self, behaviors: Mapping[str, type] | None = None) -> list[tuple[StepPath, str]]:
        """Every reason this cannot be generated, each with where it is.

        Roles are checked against lab_wizard's registered behaviors.
        """
        return [
            *super().diagnose(_behaviors() if behaviors is None else behaviors),
            *self._derived_problems(),
            *self._plot_problems(),
        ]

    # ------------------------- the data a run produces -------------------------

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
        for name in sorted(_setup_of(text)):
            if name not in self.needs:
                problems.append(f"{text!r} reads setup({name!r}), which is not declared under needs")
        tree = self.param_tree
        for path in sorted(_params_of(text)):
            decl = tree.find(path)
            if decl is None:
                problems.append(f"{text!r} reads param({path!r}), which is not declared")
            elif decl.type not in ("float", "int"):
                problems.append(f"{text!r} reads param({path!r}), which is a {decl.type}, not a number")
        try:
            compile_expression(text, columns, {0: self.param_defaults()}, {0: {name: 1.0 for name in self.needs}})
        except ExpressionError as exc:
            if not problems:
                problems.append(str(exc))
        return problems


def set_param_values(params: MutableMapping[str, Any], values: Mapping[str, Any], path: str = "") -> None:
    """Write ``values`` into a ``params:`` block, in place, as each param's ``default``.

    ``values`` is nested like the block (``{"bias": {"settle_s": 0.1}}``); a
    param it leaves out keeps its value. Each value is written as given and
    checked when the block is. A name the block lacks is a ``ValueError``.
    """
    for name, value in values.items():
        entry = params.get(name)
        if not isinstance(entry, MutableMapping):
            raise ValueError(f"no param {path}{name}")
        if isinstance(entry.get("type"), str):
            entry["default"] = value
        elif isinstance(value, Mapping):
            set_param_values(entry, value, f"{path}{name}.")
        else:
            raise ValueError(f"{path}{name} is a group of params, not one")


def carry_param_values(old: Mapping[str, Any], new: MutableMapping[str, Any]) -> None:
    """Keep ``old``'s values in ``new``, a ``params:`` block replacing it, in place.

    Each param in both, of the same type, keeps its old value; the rest take
    ``new``'s. Every value is written out, so the project states each one. For
    a project taking a newer version of its procedure.
    """
    try:
        before = ParamTree.parse(dict(old))
    except ValueError:
        before = ParamTree({})  # old values that do not parse are not worth keeping
    for dotted, decl in ParamTree.parse(dict(new)).leaves():
        kept = before.find(dotted)
        entry: Any = new
        for part in dotted.split("."):
            entry = entry[part]
        entry["default"] = kept.default if kept is not None and kept.type == decl.type else decl.default


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


def _setup_of(text: str) -> set[str]:
    try:
        return expression_setup(text)
    except ExpressionError:
        return set()


def _behaviors() -> dict[str, type]:
    # Importing the behavior modules registers them; the proxy registry imports
    # every shipped behavior.
    import lab_wizard.lib.client.proxies.registry  # noqa: F401
    from lab_wizard.lib.instruments.general.behavior import behaviors

    return dict(behaviors())
