from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TypeVar, Generic, Iterable, Set, Type, Self, Iterator, ClassVar
import inspect
from pydantic import BaseModel, model_validator, field_validator


# Move base classes above TypeVar declarations so bounds use real types (not strings)
class Dependency(ABC):
    pass


# New common base for any instrument (parent, child, or hybrid)
class Instrument(ABC):
    pass


I_co = TypeVar("I_co", bound="Child[Any, Any]", covariant=True)
P_co = TypeVar("P_co", bound="Instrument", covariant=True)
E_co = TypeVar("E_co", bound="Instrument", covariant=True)


class Params2Inst(Generic[E_co], ABC):
    """
    Mixin for parameter classes that can provide their corresponding instrument class.
    The instrument instance may require resources not included in the params object,
    such as a communication object from a parent instrument.
    """

    @property
    @abstractmethod
    def inst(self) -> type[E_co]: ...


class CanInstantiate(Generic[P_co], ABC):
    """
    An instrument can be created with the params object. No other dependencies are required.
    """

    @abstractmethod
    def create_inst(self) -> P_co:
        # this typically calls self.inst.from_params(self) or similar, possibly using internal deps
        pass


# ----------------------- KeyLike Params Mixins -----------------------


class USBLike(BaseModel):
    """Params mixin for serial/USB instruments.

    The instrument's unique key is its port string (e.g. /dev/ttyUSB0).
    Inheriting this tells config_io and the UI how to derive and apply keys.
    """

    _yaml_key_fields_: ClassVar[tuple[str, ...]] = ("port",)
    port: str = "/dev/ttyUSB0"
    key_hint: ClassVar[str] = "USB port (e.g. /dev/ttyUSB0)"

    def key_fields(self) -> str:
        return self.port

    def apply_key(self, key: str) -> None:
        self.port = key


class IPLike(BaseModel):
    """Params mixin for TCP/IP instruments.

    The instrument's unique key is ip_address:ip_port (e.g. 10.7.0.4:8345).
    Inheriting this tells config_io and the UI how to derive and apply keys.
    """

    _yaml_key_fields_: ClassVar[tuple[str, ...]] = ("ip_address", "ip_port")
    ip_address: str = "0.0.0.0"
    ip_port: int = 0
    key_hint: ClassVar[str] = "IP:port (e.g. 10.7.0.4:8345)"

    def key_fields(self) -> str:
        return f"{self.ip_address}:{self.ip_port}"

    def apply_key(self, key: str) -> None:
        host, _, port_str = key.rpartition(":")
        self.ip_address = host
        try:
            self.ip_port = int(port_str)
        except ValueError:
            pass


class SlotLike(BaseModel):
    """Params mixin for slot-addressed child instruments (e.g. SIM900 modules).

    The slot number is stored as a param field so it participates in hash
    derivation and appears in YAML alongside other settings.
    Integer values from existing YAML files are coerced to str automatically.
    """

    _yaml_key_fields_: ClassVar[tuple[str, ...]] = ("slot",)
    slot: str = "0"
    key_hint: ClassVar[str] = "Slot number (e.g. 1)"

    @field_validator("slot", mode="before")
    @classmethod
    def _coerce_slot(cls, v: Any) -> str:
        return str(v)

    def key_fields(self) -> str:
        return self.slot

    def apply_key(self, key: str) -> None:
        self.slot = key


class GPIBAddressLike(BaseModel):
    """Params mixin for instruments addressed by GPIB number on a Prologix bus.

    The GPIB address is stored as a param field so it participates in hash
    derivation and appears in YAML alongside other settings.
    Integer values from existing YAML files are coerced to str automatically.
    """

    _yaml_key_fields_: ClassVar[tuple[str, ...]] = ("gpib_address",)
    gpib_address: str = "0"
    key_hint: ClassVar[str] = "GPIB address (e.g. 4)"

    @field_validator("gpib_address", mode="before")
    @classmethod
    def _coerce_gpib(cls, v: Any) -> str:
        return str(v)

    def key_fields(self) -> str:
        return self.gpib_address

    def apply_key(self, key: str) -> None:
        self.gpib_address = key


class ChildParams(Instrument, BaseModel, Params2Inst[I_co], Generic[I_co]):
    """Base class for all child parameter objects.

    Generic over the concrete Child instrument type (I_co). This lets APIs
    accepting a ChildParams[I] return an I without ad-hoc overloads.
    """

    enabled: bool = True

    @model_validator(mode="after")
    def validate_type_exists(self) -> Self:
        """Ensure a 'type' discriminator exists (required for union discrimination)."""
        if not hasattr(self, "type") or getattr(self, "type") is None:
            raise ValueError("Missing required 'type' field")
        return self

    @property
    @abstractmethod
    def inst(self) -> type[I_co]: ...

    """
    This needs to be here even though a very similar property exist in Params2Inst. The key is that
    here we're specifying that .inst doesn't just return an Instrument, it returns specifically a Child
    """


R = TypeVar("R", bound=Dependency)
P = TypeVar("P", bound=ChildParams[Any])


PR_co = TypeVar("PR_co", bound="Parent[Any, Any]")


class ParentParams(BaseModel, Params2Inst[PR_co], Generic[PR_co, R, P]):
    """

    PR_co: Corresponding Parent instrument type
    R: Dependency type (e.g., Comm)
    P: ChildParams subtype for children

    # Use Field to avoid shared mutable default
    children: dict[str, P] = Field(default_factory=dict)
    """

    enabled: bool = True

    @model_validator(mode="after")
    def validate_type_exists(self) -> Self:
        """Ensure a 'type' discriminator exists (required for union discrimination)."""
        if not hasattr(self, "type") or getattr(self, "type") is None:
            raise ValueError("Missing required 'type' field")
        return self

    @property
    @abstractmethod
    def inst(self) -> type[PR_co]: ...


class Parent(Instrument, ABC, Generic[R, P]):
    """
    R: dependency type (e.g., Comm)
    P: ChildParams subtype for children

    The only method that must be implemented per-parent is ``make_child``,
    which creates the appropriately-scoped dependency for a child and
    constructs it. All other parent operations (make_all_children)
    have concrete default implementations here.
    """

    children: dict[str, "Child[R, P]"]

    @property
    @abstractmethod
    def dep(self) -> R:
        """
        A dep is some object that children require to operate, such as a Comm object.
        This should return the same type as the first type expected by the Child.__init__ method.
        """
        pass

    @abstractmethod
    def make_child(self, key: str) -> "Child[R, P]":
        """Create the child instrument for the given hash key.

        Implementations should:
          1. Return cached child if ``key`` is already in ``self.children``.
          2. Read ``self.params.children[key]`` to get the child's Params object.
          3. Derive the scoped dependency from the **params field** (e.g.
             ``params.gpib_address`` or ``params.slot``), NOT from ``key``.
          4. Instantiate the child with ``ChildClass(scoped_dep, params)``.
          5. Store the result in ``self.children[key]`` and return it.
        """
        pass

    def make_all_children(self) -> None:
        """Instantiate all children declared in params.children."""
        for key in list(self.params.children.keys()):  # type: ignore[attr-defined]
            if key not in self.children:
                self.make_child(key)

    # def add_child(self, params: P, key: str) -> "Child[R, P]":
    #     """Store params under key and create the child instrument.

    #     Expected behavior:
    #       - Store params into self.params.children[key]
    #       - Instantiate child via self.make_child(key)
    #       - Return the created child
    #     """
    #     self.params.children[key] = params  # type: ignore[attr-defined]
    #     return self.make_child(key)


PP = TypeVar(
    "PP", bound=ParentParams[Any, Any, Any]
)  # any concrete ParentParams subtype
PR = TypeVar("PR", bound="Parent[Any, Any]")  # any concrete Parent subtype


class ParentFactory(ABC, Generic[PP, PR]):
    """
    Factory for creating a Parent (or subtype) from its concrete ParentParams (or subtype).

    PP: concrete ParentParams subtype (any R/P specialization)
    PR: resulting Parent subtype

    from_params:
      Accepts a params instance (PP) and returns the constructed parent instrument.

    from_config:
      Concrete default — looks up the params in exp.instruments[key] and
      delegates to from_params. Root instrument classes do NOT need to
      override this.
    """

    @classmethod
    @abstractmethod
    def from_params(cls, params: PP) -> PR:
        pass

    @classmethod
    def from_config(cls, exp: Any, *, key: str) -> PR:
        """Look up hash key in exp.instruments and construct via from_params."""
        params = exp.instruments[key]
        return cls.from_params(params)  # type: ignore[arg-type]


# ------------------- Params / __init__ alignment utilities -------------------


def _collect_init_param_names(cls: type) -> Set[str]:
    """Return the set of parameter names (excluding self) in the class __init__.

    Considers POSITIONAL_OR_KEYWORD and KEYWORD_ONLY parameters. Ignores *args/**kwargs
    because those defeat strict alignment guarantees.
    """
    sig = inspect.signature(cls.__init__)
    names: Set[str] = set()
    for p in list(sig.parameters.values())[1:]:  # skip self
        if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY):
            names.add(p.name)
    return names


def assert_params_init_alignment(
    *,
    parent_cls: Type[Any],
    params_cls: Type[ParentParams[Any, Any, Any]],
    exclude: Iterable[str] = ("children", "enabled"),
    allow_missing: bool = False,
    allow_extra: bool = False,
) -> None:
    """Validate that parent_cls.__init__ parameters align with params_cls fields.

    exclude: field names in params model to ignore (e.g. children container)
    allow_missing / allow_extra: relax strictness (typically both False)

    Raises TypeError on mismatch for early (import-time) failure.
    """
    init_names = _collect_init_param_names(parent_cls)
    model_fields = set(params_cls.model_fields) - set(exclude)
    missing = model_fields - init_names
    extra = init_names - model_fields
    problems: list[str] = []
    if missing and not allow_missing:
        problems.append(f"missing field in __init__ of instrument: {sorted(missing)}")
    if extra and not allow_extra:
        problems.append(f"extra field in __init__ of instrument: {sorted(extra)}")
    if problems:
        raise TypeError(
            f"Param/init misalignment for {parent_cls.__name__} vs {params_cls.__name__}: "
            + "; ".join(problems)
        )


C = TypeVar("C", bound="Child[Any, Any]")
# Replace old Child with param-generic version
P_child = TypeVar("P_child", bound=ChildParams[Any])


class Child(Instrument, ABC, Generic[R, P_child]):
    """Generic child instrument / module interface.

    R: dependency type passed down by the parent. The child may make a new dep
    object for internal use, using the parent's key that refers to this child.

    P_child: concrete ChildParams subtype describing configuration for this child

    ``from_config`` is a concrete classmethod here — child classes do NOT need
    to override it. The pattern is always: check cache → delegate to
    ``parent.make_child(key)`` → type-check → return.
    """

    @property
    @abstractmethod
    def parent_class(self) -> str:
        """Fully-qualified (or uniquely identifying) name of the expected parent class."""
        pass

    @classmethod
    def from_config(cls: type[C], parent: Any, *, key: str) -> C:
        """Construct or retrieve the child instrument for the given hash key.

        Checks the parent's children cache first; if not present, delegates
        to ``parent.make_child(key)``. No per-child override needed.
        """
        existing = getattr(parent, "children", {}).get(key)
        if existing is not None:
            if not isinstance(existing, cls):
                raise TypeError(
                    f"Expected {cls.__name__} child at {key!r}, "
                    f"got {type(existing).__name__}"
                )
            return existing
        child = parent.make_child(key)
        if not isinstance(child, cls):
            raise TypeError(
                f"parent.make_child({key!r}) returned {type(child).__name__}, "
                f"expected {cls.__name__}"
            )
        return child


# ----------------------- ChannelProvider Mixin -----------------------

ChanT = TypeVar("ChanT")


class ChannelProvider(ABC, Generic[ChanT]):
    """Mixin for any instrument that internally manages a fixed collection of channel objects.

    Provides a small convenience API and an abstract contract that ``channels`` exists.
    Instruments like Sim970, Dac4D, Dac16D inherit from this to guarantee a stable
    interface for higher-level code (measurement orchestration, UI, etc.).
    """

    # Subclasses must set: self.channels: list[ChanT]
    channels: list[ChanT]

    @property
    def num_channels(self) -> int:
        return len(self.channels)

    def get_channel(self, index: int) -> ChanT:
        if index < 0 or index >= len(self.channels):
            raise IndexError(
                f"Channel index {index} out of range (0..{len(self.channels)-1})"
            )
        return self.channels[index]

    def __getitem__(self, index: int) -> ChanT:  # allows obj[index]
        return self.get_channel(index)

    def __iter__(self) -> Iterator[ChanT]:
        return iter(self.channels)

    def iter_channels(self) -> Iterator[ChanT]:
        return iter(self.channels)


# Public export surface
__all__ = [
    "Dependency",
    "ChildParams",
    "ParentParams",
    "Parent",
    "ParentFactory",
    "Child",
    "ChannelProvider",
    "assert_params_init_alignment",
    "CanInstantiate",
    "USBLike",
    "IPLike",
    "SlotLike",
    "GPIBAddressLike",
]
