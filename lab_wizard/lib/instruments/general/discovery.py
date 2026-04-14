"""Typed instrument discovery system.

Instrument Params classes inherit ``Discoverable`` to expose self-describing
discovery actions (probe connections, scan buses, populate children, etc.)
that the wizard UI renders dynamically.

Every action declares:
    - a pydantic ``params_model`` describing its inputs (field names, types,
      defaults, and labels — the frontend form is derived from this),
    - a handler method whose signature documents the exact params shape and
      whose return annotation declares the result shape.

The set of valid result shapes is a closed discriminated union
(``DiscoveryResult``) so the frontend can branch on ``result_type`` with full
type safety.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, Callable, Generic, Literal, TypeVar, get_type_hints

from pydantic import BaseModel, Field
from pydantic_core import PydanticUndefined


# ---------------------------------------------------------------------------
# Result models — one per result_type, merged into a discriminated union.
# ---------------------------------------------------------------------------


class ProbeFound(BaseModel):
    port: str
    description: str | None = None


class ProbeResult(BaseModel):
    """Connection/port discovery — user picks one entry, its ``port`` becomes the key."""

    result_type: Literal["probe"] = "probe"
    found: list[ProbeFound]


class DiscoveredChild(BaseModel):
    type: str
    key_fields: dict[str, str]
    idn: str | None = None


class ChildrenResult(BaseModel):
    """Sub-instruments found under a parent — applied automatically after confirm."""

    result_type: Literal["children"] = "children"
    children: list[DiscoveredChild]
    parent_key: str | None = None


class SelfCandidate(BaseModel):
    key_fields: dict[str, str]
    idn: str | None = None


class SelfCandidatesResult(BaseModel):
    """Instances of this instrument found on a bus — user picks one."""

    result_type: Literal["self_candidates"] = "self_candidates"
    found: list[SelfCandidate]


DiscoveryResult = Annotated[
    ProbeResult | ChildrenResult | SelfCandidatesResult,
    Field(discriminator="result_type"),
]


# ---------------------------------------------------------------------------
# Action + input specs — the JSON-serializable descriptors the frontend sees.
# ---------------------------------------------------------------------------


class DiscoveryInputSpec(BaseModel):
    name: str
    type: Literal["text", "number"]
    label: str
    default: Any = None


class DiscoveryActionSpec(BaseModel):
    name: str
    label: str
    description: str
    inputs: list[DiscoveryInputSpec] = []
    parent_dep: str | None = None
    result_type: Literal["probe", "children", "self_candidates"]


class NoParams(BaseModel):
    """Empty params model for handlers that take no user input."""


# ---------------------------------------------------------------------------
# DiscoveryAction — runtime container binding metadata + handler.
# ---------------------------------------------------------------------------


P = TypeVar("P", bound=BaseModel)
R = TypeVar("R", bound=BaseModel)


@dataclass
class DiscoveryAction(Generic[P, R]):
    """A discovery action bound to a typed handler.

    The handler's signature is authoritative: ``params_model`` names the
    expected input shape, and the handler's return annotation names the
    result shape. ``to_spec()`` derives the JSON-serializable descriptor
    sent to the frontend from these, so there is no second source of truth.

    Handler signatures:
        - ``(params: P) -> R``                for actions without ``parent_dep``
        - ``(params: P, parent: Any) -> R``   for actions with ``parent_dep``
    """

    name: str
    label: str
    description: str
    params_model: type[P]
    handler: Callable[..., R]
    parent_dep: str | None = None

    def to_spec(self) -> DiscoveryActionSpec:
        return DiscoveryActionSpec(
            name=self.name,
            label=self.label,
            description=self.description,
            inputs=self._inputs_from_model(),
            parent_dep=self.parent_dep,
            result_type=self._infer_result_type(),
        )

    def run(self, raw_params: dict[str, Any], *, parent: Any = None) -> BaseModel:
        params = self.params_model.model_validate(raw_params)
        if self.parent_dep is not None:
            if parent is None:
                raise ValueError(
                    f"Action {self.name!r} requires a {self.parent_dep!r} parent"
                )
            return self.handler(params, parent)
        return self.handler(params)

    def _inputs_from_model(self) -> list[DiscoveryInputSpec]:
        inputs: list[DiscoveryInputSpec] = []
        for fname, finfo in self.params_model.model_fields.items():
            inputs.append(
                DiscoveryInputSpec(
                    name=fname,
                    type=_ui_type_for(finfo.annotation),
                    label=finfo.description or fname,
                    default=(
                        finfo.default if finfo.default is not PydanticUndefined else None
                    ),
                )
            )
        return inputs

    def _infer_result_type(self) -> Literal["probe", "children", "self_candidates"]:
        func = getattr(self.handler, "__func__", self.handler)
        hints = get_type_hints(func)
        ret = hints.get("return")
        if ret is None or not isinstance(ret, type) or not issubclass(ret, BaseModel):
            raise TypeError(
                f"Handler {self.handler!r} must have a BaseModel return annotation"
            )
        return ret.model_fields["result_type"].default


def _ui_type_for(annotation: Any) -> Literal["text", "number"]:
    return "number" if annotation in (int, float) else "text"


# ---------------------------------------------------------------------------
# Discoverable mixin.
# ---------------------------------------------------------------------------


class Discoverable:
    """Mixin for Params classes that expose discovery actions."""

    @classmethod
    def discovery_actions(cls) -> list[DiscoveryAction[Any, Any]]:
        return []


# ---------------------------------------------------------------------------
# Shared helper.
# ---------------------------------------------------------------------------


def get_idn(controller_dep: Any, address: int) -> str:
    """Query *IDN? from a GPIB-addressed instrument.

    Returns empty string on any error.
    """
    try:
        raw = controller_dep.query_instrument(address, "*IDN?")
        return (
            raw.decode(errors="replace").strip()
            if isinstance(raw, bytes)
            else str(raw).strip()
        )
    except Exception:
        return ""
