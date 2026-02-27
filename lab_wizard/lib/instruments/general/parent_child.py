from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, TypeVar, Generic, Iterable, Set, Type, Self, Iterator, ClassVar
import inspect
from pydantic import BaseModel, model_validator


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
    """Params mixin for slot/GPIB-addressed child instruments.

    The key is the slot or GPIB address supplied externally by the parent
    at instantiation time; no field needs to be updated.
    """

    key_hint: ClassVar[str] = "Slot or GPIB address (e.g. 4)"

    def key_fields(self) -> str:
        return ""

    def apply_key(self, key: str) -> None:
        pass


# hmm I want ParentParams to have a method create_inst() IFF it is a top parent.
# if its not a top parent then it presumably needs to pass down a resource like a comm object


# and I want


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

    @property
    @abstractmethod
    def inst(self) -> type[PR_co]: ...


class Parent(Instrument, ABC, Generic[R, P]):
    """
    R: dependency type (e.g., Comm)
    P: ChildParams subtype for children

    Note:
      The factory method for creating a Parent from only ParentParams has been
      split out into ParentFactory. This avoids a signature clash when a class
      is both a Parent and a Child (hybrid). Hybrid classes now only need to
      implement the Child.from_params(dep, params) factory.
    """

    children: dict[str, "Child[R, P]"]

    @property
    @abstractmethod
    def dep(self) -> R:
        """
        A dep is some object that children require to operate, such as a Comm object.
        This should return the same type as the first type expected by the Child.from_params method.
        """
        pass

    @abstractmethod
    def init_child_by_key(self, key: str) -> "Child[R, P]":
        """
        Create for a key 'my_key', init a child from this.params.children['my_key'],
        and place it in self.children['my_key'].
        """
        pass

    @abstractmethod
    def init_children(self) -> None:
        """
        This is intended to enable the 'automatic' creation of all children by
        iterating over the self.params.children dict. And therefore filling
        the self.children dict.
        """
        pass

    @abstractmethod
    def add_child(self, params: P, key: str) -> "Child[R, Any]":
        """Add a child by key with the provided params and return the instance.

        Expected behavior:
          - Store params into self.params.children[key]
          - Instantiate child via params.inst.from_params_with_dep(self.dep, key, params)
          - Record in self.children[key]
          - Return the created child

        Note: Subclasses should use a generic TypeVar to return more specific types.
        """
        pass


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
      Accepts a params instance (PP) and returns (parent_instance, same_params_object).
    """

    @classmethod
    @abstractmethod
    def from_params(cls, params: PP) -> PR:
        pass


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

    Subclasses implement from_params with their concrete (R, P) and return their
    own class type. The generic signature with the "cls: type[C]" pattern allows
    precise return typing while remaining friendly to static type checkers.
    """

    @property
    @abstractmethod
    def parent_class(self) -> str:
        """Fully-qualified (or uniquely identifying) name of the expected parent class."""
        pass

    @classmethod
    @abstractmethod
    def from_params_with_dep(
        cls: type[C],
        parent_dep: R,
        key: str,
        params: P_child,
    ) -> C:
        """Factory constructing the child from dependency + params.

        It's the job of the child in this function to create its own form of the dependency,
        using the dependency object provided by the parent.
        """


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
]
