"""lab_wizard's procedure definition: lab_procedure's, plus how a run is looked at.

A procedure is stored as YAML under ``config/procedures/<name>.yml`` (see
:mod:`lab_procedure.definition` for the roles, params and body). lab_wizard
adds what its data layer reads:

* ``plots`` — what a run of this procedure is usually looked at as;
* ``derived`` — columns computed from the recorded ones when read;

and checks roles against the registered instrument behaviors.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import Field, field_validator

from lab_procedure import ProcedureDefinition as BaseProcedureDefinition
from lab_procedure import ProcedureError
from lab_procedure.schema import StepPath

from lab_wizard.lib.data.expressions import (
    ExpressionError,
    compile_expression,
    expression_names,
    expression_params,
)
from lab_wizard.lib.data.plot import PlotSpec


__all__ = ["ProcedureDefinition"]

# Names a role cannot take, because the generated code already uses them.
_RESERVED_ROLES = {"resources", "project"}


class ProcedureDefinition(BaseProcedureDefinition):
    """A procedure as lab_wizard stores it. :meth:`check` says whether it can be generated."""

    # What a run of this procedure is usually looked at as. The first is what
    # the Data page and a live plotter draw by default.
    plots: list[PlotSpec] = Field(default_factory=list)
    # Columns computed from the recorded ones when read, never stored:
    # {"above_dark": 'count_rate - mean(count_rate, phase == "background")'}.
    derived: dict[str, str] = Field(default_factory=dict)

    @field_validator("roles")
    @classmethod
    def _roles_not_reserved(cls, roles: dict) -> dict:
        for name in roles:
            if name in _RESERVED_ROLES:
                raise ValueError(f"Role {name!r} is reserved by the generated code")
        return roles

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


def _behaviors() -> dict[str, type]:
    # Importing the behavior modules registers them; the proxy registry imports
    # every shipped behavior.
    import lab_wizard.lib.client.proxies.registry  # noqa: F401
    from lab_wizard.lib.instruments.general.behavior import behaviors

    return dict(behaviors())
