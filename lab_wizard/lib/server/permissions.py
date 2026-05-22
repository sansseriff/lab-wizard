"""Permission state machine for the lab_wizard server.

Model: a **rule list with declarative state**. Instruments declare which of
their methods influence safety-relevant state; after each successful RPC the
server records the new state value. Before dispatching any RPC the server
evaluates the rule list against current state and denies the call if a rule's
``when`` condition is satisfied and the call matches one of the rule's ``deny``
clauses.

State observation is opt-in per instrument via a ``_state_methods_`` class
attribute:

    class Sim928(Child, VSource):
        _state_methods_ = {
            "set_voltage": ("voltage", Arg(0)),  # store args[0] under "voltage"
            "turn_on":     ("output",  "on"),    # store the literal "on"
            "turn_off":    ("output",  "off"),
        }

Each entry maps ``method_name -> (state_key, value_spec)`` where ``value_spec``
is one of:
    - ``Arg(i)``  — store the i-th positional argument
    - ``Kwarg(name)`` — store the named keyword argument
    - ``Result()`` — store the method's return value
    - anything else — stored as a literal value

State is keyed by ``(inst_path, state_key)``. A rule's ``when`` condition reads
those state values; the leaf predicate references ``path`` + ``key``.

Config schema (under ``permissions:`` in server.yaml):

    permissions:
      state_defaults:
        "inst://.../channel/0": { output: "off" }
      rules:
        - id: cryo_amp_safety
          description: "..."
          when:
            all:
              - { path: "inst://.../channel/0", key: output, equals: "on" }
          deny:
            - { path_glob: "inst://*/funcgen/*", methods: [pulse, burst] }
            - { path: "inst://.../channel/2", methods: [set_voltage] }
          message: "Cryo amp is biased on; disable channel 0 before pulsing."
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field, model_validator

# Re-exported for convenience; the canonical definitions live in the
# instrument layer so instruments never import server code.
from lab_wizard.lib.instruments.general.state_effects import (
    Arg,
    Kwarg,
    Result,
    collect_state_methods,
    resolve_state_value,
)


__all__ = [
    "Arg",
    "Kwarg",
    "Result",
    "Condition",
    "DenyClause",
    "Rule",
    "PermissionsConfig",
    "StateTracker",
    "Denial",
    "PermissionGate",
    "load_permissions",
    "resolve_attributes",
]


# --------------------------- condition tree ---------------------------


class Condition(BaseModel):
    """A composable predicate over recorded state.

    Exactly one mode is active:
      - composite: ``all`` / ``any`` / ``not`` (recursive)
      - leaf: (``path`` OR ``attribute``) + ``key`` + one comparison operator

    A leaf may reference its instrument by raw ``path`` (``inst://...``) or by
    the stable ``attribute`` name. ``attribute`` is resolved to ``path`` at gate
    construction (see :func:`resolve_attributes`); ``attribute`` is preferred
    because it survives key-field edits that change the derived hash path.
    """

    # composite
    all_: Optional[list["Condition"]] = Field(default=None, alias="all")
    any_: Optional[list["Condition"]] = Field(default=None, alias="any")
    not_: Optional["Condition"] = Field(default=None, alias="not")

    # leaf reference: exactly one of path / attribute, plus key
    path: Optional[str] = None
    attribute: Optional[str] = None
    key: Optional[str] = None

    # leaf comparisons (at most one set)
    equals: Optional[Any] = None
    not_equals: Optional[Any] = None
    greater_than: Optional[float] = None
    less_than: Optional[float] = None
    in_: Optional[list[Any]] = Field(default=None, alias="in")

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _validate_shape(self) -> "Condition":
        composites = [self.all_, self.any_, self.not_]
        is_composite = any(c is not None for c in composites)
        is_leaf = (
            self.path is not None or self.attribute is not None or self.key is not None
        )
        if is_composite and is_leaf:
            raise ValueError("Condition cannot be both composite and leaf")
        if not is_composite and not is_leaf:
            raise ValueError(
                "Condition must be a composite (all/any/not) or a leaf "
                "((path|attribute)+key)"
            )
        if is_leaf:
            if self.key is None:
                raise ValueError("Leaf condition requires 'key'")
            if (self.path is None) == (self.attribute is None):
                raise ValueError(
                    "Leaf condition requires exactly one of 'path' or 'attribute'"
                )
        return self

    def evaluate(self, state: "StateTracker") -> bool:
        if self.all_ is not None:
            return all(c.evaluate(state) for c in self.all_)
        if self.any_ is not None:
            return any(c.evaluate(state) for c in self.any_)
        if self.not_ is not None:
            return not self.not_.evaluate(state)
        # leaf
        if self.path is None:
            raise ValueError(
                f"Condition references unresolved attribute {self.attribute!r}; "
                "resolve_attributes() must run before evaluation."
            )
        assert self.key is not None
        value = state.get(self.path, self.key)
        return self._compare(value)

    def _compare(self, value: Any) -> bool:
        if self.equals is not None:
            return value == self.equals
        if self.not_equals is not None:
            return value != self.not_equals
        if self.greater_than is not None:
            return isinstance(value, (int, float)) and value > self.greater_than
        if self.less_than is not None:
            return isinstance(value, (int, float)) and value < self.less_than
        if self.in_ is not None:
            return value in self.in_
        # No operator → truthiness of the state value (e.g. a bool flag)
        return bool(value)


# --------------------------- rules ---------------------------


class DenyClause(BaseModel):
    """Matches a (path, method) pair to gate.

    Specify exactly one of ``path``, ``path_glob``, or ``attribute``.
    ``attribute`` is resolved to an exact ``path`` at gate construction (see
    :func:`resolve_attributes`) and is the stable, recommended form.
    """

    path: Optional[str] = None
    path_glob: Optional[str] = None
    attribute: Optional[str] = None
    methods: list[str]

    @model_validator(mode="after")
    def _validate(self) -> "DenyClause":
        forms = [self.path, self.path_glob, self.attribute]
        if sum(f is not None for f in forms) != 1:
            raise ValueError(
                "DenyClause requires exactly one of 'path', 'path_glob', or 'attribute'"
            )
        return self

    def matches(self, path: str, method: str) -> bool:
        if method not in self.methods:
            return False
        if self.attribute is not None and self.path is None:
            raise ValueError(
                f"DenyClause references unresolved attribute {self.attribute!r}; "
                "resolve_attributes() must run before matching."
            )
        if self.path is not None:
            return path == self.path
        return fnmatch.fnmatch(path, self.path_glob or "")


class Rule(BaseModel):
    id: str
    description: str = ""
    when: Condition
    deny: list[DenyClause]
    message: str = ""


class PermissionsConfig(BaseModel):
    """Parsed ``permissions:`` block. Blocklist model: allow unless denied."""

    state_defaults: dict[str, dict[str, Any]] = Field(default_factory=dict)
    rules: list[Rule] = Field(default_factory=list)


# --------------------------- state + gate ---------------------------


class StateTracker:
    """Holds ``(inst_path, state_key) -> value``, seeded with defaults."""

    def __init__(self, defaults: Optional[dict[str, dict[str, Any]]] = None) -> None:
        self._state: dict[tuple[str, str], Any] = {}
        for path, kv in (defaults or {}).items():
            for key, value in kv.items():
                self._state[(path, key)] = value

    def get(self, path: str, key: str) -> Any:
        return self._state.get((path, key))

    def record(
        self,
        path: str,
        obj: Any,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        """Update state if the resolved object declares this method stateful.

        ``_state_methods_`` is merged across the object's MRO, so declarations on
        a behavior ABC (e.g. VSource) apply to subclasses unless overridden.
        """
        spec_map = collect_state_methods(type(obj))
        if method not in spec_map:
            return
        key, value_spec = spec_map[method]
        self._state[(path, key)] = resolve_state_value(value_spec, args, kwargs, result)

    def snapshot(self) -> dict[str, Any]:
        """Flat ``"path#key": value`` view (JSON-safe), for diagnostics/errors."""
        return {f"{p}#{k}": v for (p, k), v in self._state.items()}


@dataclass
class Denial:
    """Returned by ``PermissionGate.check`` when a call is blocked."""

    rule_id: str
    message: str
    blocking_state: dict[str, Any]


class PermissionGate:
    """Evaluates rules before dispatch and records state after dispatch."""

    def __init__(
        self,
        config: Optional[PermissionsConfig] = None,
        attribute_resolver: Optional["Callable[[str], str]"] = None,
    ) -> None:
        self._config = config or PermissionsConfig()
        if attribute_resolver is not None:
            resolve_attributes(self._config, attribute_resolver)
        self._tracker = StateTracker(self._config.state_defaults)

    @property
    def tracker(self) -> StateTracker:
        return self._tracker

    def check(
        self,
        path: str,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
    ) -> Optional[Denial]:
        """Return a ``Denial`` if any rule blocks this call, else ``None``."""
        for rule in self._config.rules:
            if not any(clause.matches(path, method) for clause in rule.deny):
                continue
            if rule.when.evaluate(self._tracker):
                msg = rule.message or f"Blocked by rule {rule.id!r}"
                return Denial(
                    rule_id=rule.id,
                    message=msg,
                    blocking_state=self._relevant_state(rule),
                )
        return None

    def record(
        self,
        path: str,
        obj: Any,
        method: str,
        args: list[Any],
        kwargs: dict[str, Any],
        result: Any,
    ) -> None:
        self._tracker.record(path, obj, method, args, kwargs, result)

    def _relevant_state(self, rule: Rule) -> dict[str, Any]:
        """Collect the (path, key) values the rule's condition references."""
        refs: dict[str, Any] = {}

        def walk(cond: Condition) -> None:
            if cond.all_ is not None:
                for c in cond.all_:
                    walk(c)
            elif cond.any_ is not None:
                for c in cond.any_:
                    walk(c)
            elif cond.not_ is not None:
                walk(cond.not_)
            elif cond.path is not None and cond.key is not None:
                refs[f"{cond.path}#{cond.key}"] = self._tracker.get(cond.path, cond.key)

        walk(rule.when)
        return refs


Condition.model_rebuild()


def load_permissions(data: Optional[dict[str, Any]]) -> PermissionsConfig:
    """Build a PermissionsConfig from the ``permissions:`` mapping (or empty)."""
    if not data:
        return PermissionsConfig()
    return PermissionsConfig.model_validate(data)


def resolve_attributes(
    config: PermissionsConfig, resolver: Callable[[str], str]
) -> None:
    """Rewrite ``attribute`` references to concrete ``path`` values in-place.

    Walks every rule's condition tree and deny clauses; for any leaf/clause that
    references an instrument by ``attribute`` (and has no explicit ``path``),
    looks up the ``inst://`` path via ``resolver`` and stores it on ``.path``.
    Conditions/clauses authored with raw ``path``/``path_glob`` are untouched.

    Raises ``ValueError`` (wrapping the resolver's lookup error) if an attribute
    name cannot be resolved, so misconfiguration fails loudly at startup rather
    than silently disabling a safety rule.
    """

    def _resolve_one(attribute: str, where: str) -> str:
        try:
            return resolver(attribute)
        except KeyError as exc:
            raise ValueError(
                f"Permission rule references unknown attribute_name "
                f"{attribute!r} ({where}). {exc}"
            ) from exc

    def _walk_condition(cond: Condition, rule_id: str) -> None:
        if cond.all_ is not None:
            for c in cond.all_:
                _walk_condition(c, rule_id)
        elif cond.any_ is not None:
            for c in cond.any_:
                _walk_condition(c, rule_id)
        elif cond.not_ is not None:
            _walk_condition(cond.not_, rule_id)
        elif cond.attribute is not None and cond.path is None:
            cond.path = _resolve_one(
                cond.attribute, f"when-condition of rule {rule_id!r}"
            )

    for rule in config.rules:
        _walk_condition(rule.when, rule.id)
        for clause in rule.deny:
            if clause.attribute is not None and clause.path is None:
                clause.path = _resolve_one(
                    clause.attribute, f"deny clause of rule {rule.id!r}"
                )
