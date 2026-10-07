"""Derived quantities: a small expression language compiled to polars.

``count_rate - mean(count_rate, phase == "background")`` is a background-
subtracted rate; ``x / max(x)`` normalizes each run to its own peak. The
language, completely (``plans/semantic_data_plan.md`` §10):

- numbers, strings, and column names (recorded or derived);
- ``+ - * / **``, unary ``-``, parentheses;
- row functions ``abs sqrt exp log log10``;
- per-run reductions ``mean min max sum count first last``, each ``f(expr)`` or
  ``f(expr, condition)``, where a condition may use ``== != < <= > >=`` and
  ``and``, ``or``, ``not``;
- ``param("bias.settle_s")``: the value a run's own params gave;
- ``setup("bias_resistance")``: one of the procedure's needs, read from the
  run's own copy of its setup in the need's unit (``plans/setup_plan.md``), so
  ``(bias_voltage - sense_voltage) / setup("bias_resistance")`` is a current in
  every run, whatever resistor each was taken with.

Reductions are computed per run (``.over("run_id")``), so an expression means
the same thing over one run or fifty. The text is parsed with Python's ``ast``
and only the node types above are accepted; it is never evaluated as Python.
"""

from __future__ import annotations

import ast
from collections.abc import Iterable, Mapping
from typing import Any

import polars as pl

__all__ = [
    "ExpressionError",
    "compile_expression",
    "derive",
    "expression_names",
    "expression_params",
    "expression_setup",
]

Params = Mapping[int, Mapping[str, Any]]
# {run_id: {need: value}}, as ``Runs.setup_values`` reads it.
SetupValues = Mapping[int, Mapping[str, float]]

RUN = "run_id"

_BINARY = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
}
_COMPARE = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}
_ROW_FUNCTIONS = {
    "abs": lambda e: e.abs(),
    "sqrt": lambda e: e.sqrt(),
    "exp": lambda e: e.exp(),
    "log": lambda e: e.log(),
    "log10": lambda e: e.log10(),
}
_REDUCTIONS = {
    "mean": lambda e: e.mean(),
    "min": lambda e: e.min(),
    "max": lambda e: e.max(),
    "sum": lambda e: e.sum(),
    "count": lambda e: e.count(),
    "first": lambda e: e.first(),
    "last": lambda e: e.last(),
}


class ExpressionError(ValueError):
    """An expression uses something outside the language, or an unknown column."""


def _parse(text: str) -> ast.expr:
    try:
        return ast.parse(text.strip(), mode="eval").body
    except SyntaxError as exc:
        raise ExpressionError(f"{text!r} is not an expression: {exc.msg}") from None


def expression_names(text: str) -> set[str]:
    """The column names ``text`` refers to (function names excluded)."""
    tree = _parse(text)
    called = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and id(n) not in called}


def _quoted_arguments(text: str, function: str) -> set[str]:
    return {
        n.args[0].value
        for n in ast.walk(_parse(text))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == function
        and len(n.args) == 1 and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
    }


def expression_params(text: str) -> set[str]:
    """The param paths ``text`` reads with ``param("...")``."""
    return _quoted_arguments(text, "param")


def expression_setup(text: str) -> set[str]:
    """The needs ``text`` reads with ``setup("...")``."""
    return _quoted_arguments(text, "setup")


def _lookup(params: Mapping[str, Any], path: str) -> Any:
    node: Any = params
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return None
        node = node[part]
    return node


class _Compiler:
    def __init__(self, text: str, columns: Iterable[str], params: Params | None, setup: SetupValues | None) -> None:
        self.text = text
        self.columns = set(columns)
        self.params = params
        self.setup = setup

    def fail(self, what: str) -> ExpressionError:
        return ExpressionError(f"{self.text!r}: {what}")

    def value(self, node: ast.expr) -> pl.Expr:
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)) and not isinstance(node.value, bool):
            return pl.lit(node.value)
        if isinstance(node, ast.Name):
            if node.id not in self.columns:
                known = ", ".join(sorted(self.columns)) or "none"
                raise self.fail(f"no column named {node.id!r} (columns: {known})")
            return pl.col(node.id)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
            return _BINARY[type(node.op)](self.value(node.left), self.value(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            operand = self.value(node.operand)
            return -operand if isinstance(node.op, ast.USub) else operand
        if isinstance(node, ast.Call):
            return self.call(node)
        if isinstance(node, (ast.Compare, ast.BoolOp)) or (isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not)):
            raise self.fail("a comparison is only allowed as a reduction's condition, e.g. mean(x, phase == \"signal\")")
        raise self.fail(f"{ast.unparse(node)!r} is not part of the expression language")

    def condition(self, node: ast.expr) -> pl.Expr:
        if isinstance(node, ast.Compare):
            if len(node.ops) != 1 or type(node.ops[0]) not in _COMPARE:
                raise self.fail(f"{ast.unparse(node)!r}: compare two things with one of == != < <= > >=")
            return _COMPARE[type(node.ops[0])](self.value(node.left), self.value(node.comparators[0]))
        if isinstance(node, ast.BoolOp):
            parts = [self.condition(v) for v in node.values]
            out = parts[0]
            for part in parts[1:]:
                out = (out & part) if isinstance(node.op, ast.And) else (out | part)
            return out
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return ~self.condition(node.operand)
        raise self.fail(f"{ast.unparse(node)!r} is not a condition")

    def call(self, node: ast.Call) -> pl.Expr:
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if node.keywords:
            raise self.fail(f"{name or 'a function'}() takes no keyword arguments")
        if name == "param":
            return self.param(node)
        if name == "setup":
            return self.setup_value(node)
        if name in _ROW_FUNCTIONS:
            if len(node.args) != 1:
                raise self.fail(f"{name}() takes one argument")
            return _ROW_FUNCTIONS[name](self.value(node.args[0]))
        if name in _REDUCTIONS:
            if len(node.args) not in (1, 2):
                raise self.fail(f"{name}() takes an expression and, optionally, a condition")
            inner = self.value(node.args[0])
            if len(node.args) == 2:
                inner = inner.filter(self.condition(node.args[1]))
            return _REDUCTIONS[name](inner).over(RUN)
        functions = ", ".join(sorted({*_ROW_FUNCTIONS, *_REDUCTIONS, "param", "setup"}))
        raise self.fail(f"unknown function {name or ast.unparse(node.func)!r} (functions: {functions})")


    def param(self, node: ast.Call) -> pl.Expr:
        if len(node.args) != 1 or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            raise self.fail('param() takes one quoted param path, e.g. param("readout.gate_time_s")')
        path = node.args[0].value
        if self.params is None:
            raise self.fail(f"param({path!r}) needs the runs' params, and none were given")
        values = {}
        for run_id, params in self.params.items():
            value = _lookup(params, path)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise self.fail(f"param({path!r}) is {value!r} in run {run_id}; only a number can be used")
            values[run_id] = value
        # Each row takes the value from its own run's params.
        return pl.col(RUN).replace_strict(values, default=None, return_dtype=pl.Float64)

    def setup_value(self, node: ast.Call) -> pl.Expr:
        if len(node.args) != 1 or not (isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
            raise self.fail('setup() takes one quoted need, e.g. setup("bias_resistance")')
        name = node.args[0].value
        if self.setup is None:
            raise self.fail(f"setup({name!r}) needs the runs' setups, and none were given")
        values = {run_id: needs.get(name) for run_id, needs in self.setup.items()}
        # Each row takes the value from its own run's copy of its setup.
        return pl.col(RUN).replace_strict(values, default=None, return_dtype=pl.Float64)


def compile_expression(
    text: str, columns: Iterable[str], params: Params | None = None, setup: SetupValues | None = None
) -> pl.Expr:
    """``text`` as a polars expression over ``columns``.

    Evaluate it on a frame with a ``run_id`` column, sorted by run and
    recording order, so ``first`` and ``last`` mean the first and last row.
    ``params`` is ``{run_id: params}``, for ``param("...")``, and ``setup``
    ``{run_id: {need: value}}``, for ``setup("...")``.
    """
    return _Compiler(text, columns, params, setup).value(_parse(text))


def derive(
    frame: pl.DataFrame,
    derived: Mapping[str, str],
    params: Params | None = None,
    setup: SetupValues | None = None,
) -> pl.DataFrame:
    """``frame`` with a column per ``{name: expression}``, in dependency order.

    A derived column may use another; a cycle, or a name that is already a
    recorded column, is an error.
    """
    clashes = sorted(set(derived) & set(frame.columns))
    if clashes:
        raise ExpressionError(f"derived {', '.join(map(repr, clashes))} would replace a recorded column")
    pending = dict(derived)
    while pending:
        # Ready once nothing it refers to is still waiting to be derived.
        ready = [name for name, text in pending.items() if not expression_names(text) & set(pending)]
        if not ready:
            raise ExpressionError(f"derived columns refer to each other in a cycle: {', '.join(sorted(pending))}")
        frame = frame.with_columns(
            compile_expression(pending[name], frame.columns, params, setup).alias(name) for name in ready
        )
        for name in ready:
            del pending[name]
    return frame
