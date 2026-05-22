"""Instrument tree indexing for the server.

Two construction modes:

- **Lazy, config-dir hosting** (``from_config_dir`` / ``from_instruments``): the
  server's primary mode. It walks the *params* tree (``load_instruments`` output)
  without touching hardware, building the ``inst://`` path index, the
  ``attribute_name`` index, and static per-path metadata (``behavior_abc`` /
  ``type_hint``) derived from the params' instrument classes. Live instrument
  objects are instantiated **on first ``resolve``** and cached, so booting the
  daemon (or merely listing/describing what it can provide) never opens a serial
  port or GPIB connection.

- **Eager, single-``Exp`` hosting** (``InstrumentRegistry(exp)``): the original
  Phase-1 behavior, kept for the optional per-project ``exp_yaml`` override and
  for tests. It instantiates every instrument up front.

Path scheme:
    inst://<root_key>[/<child_key>]*[/channel/<idx>]

Examples:
    inst://2da0863e                          (DBay root)
    inst://2da0863e/a0da5bfa                 (Dac4D child)
    inst://2da0863e/a0da5bfa/channel/1       (Dac4DChannel — VSource)
    inst://7bf897f7/d6f1dcc9/c7fe1259        (Sim970 child)
    inst://7bf897f7/d6f1dcc9/c7fe1259/channel/0  (Sim970Channel — VSense)
"""

from __future__ import annotations

from typing import Any, Callable, Optional, get_args, get_origin

from lab_wizard.lib.instruments.general.parent_child import ChannelProvider
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.utilities.model_tree import Exp


PATH_PREFIX = "inst://"


# Order matters: most specific terminal behavior first. ``describe_*`` returns
# the first ABC whose check matches.
_BEHAVIOR_ABCS: tuple[tuple[str, type], ...] = (
    ("VSource", VSource),
    ("VSense", VSense),
    ("ChannelProvider", ChannelProvider),
)


def _behavior_abc_name(obj: Any) -> str | None:
    """Behavior ABC of a live object (isinstance check)."""
    for name, abc in _BEHAVIOR_ABCS:
        if isinstance(obj, abc):
            return name
    return None


def _behavior_abc_for_class(cls: type | None) -> str | None:
    """Behavior ABC of an instrument *class* (issubclass check, no instance)."""
    if cls is None:
        return None
    for name, abc in _BEHAVIOR_ABCS:
        try:
            if issubclass(cls, abc):
                return name
        except TypeError:
            continue
    return None


def _channel_class(parent_inst_cls: type | None) -> type | None:
    """Extract the channel element class from ``ChannelProvider[Chan]``.

    A ``ChannelProvider`` subclass parameterizes the generic with its channel
    type (e.g. ``ChannelProvider[Dac4DChannel]``); we read that argument from
    ``__orig_bases__`` so the channel's behavior ABC can be determined without
    instantiating the parent.
    """
    if parent_inst_cls is None:
        return None
    for base in getattr(parent_inst_cls, "__orig_bases__", ()):
        origin = get_origin(base)
        if origin is None:
            continue
        try:
            if issubclass(origin, ChannelProvider):
                args = get_args(base)
                if args and isinstance(args[0], type):
                    return args[0]
        except TypeError:
            continue
    return None


class InstrumentRegistry:
    """Index from ``inst://`` path to instrument object, with lazy hosting."""

    def __init__(self, exp: Exp) -> None:
        # Eager mode: instantiate the whole tree from a single Exp (override /
        # tests). Lazy mode is built via the ``from_*`` classmethods below.
        self._index: dict[str, Any] = {}
        self._attribute_index: dict[str, str] = {}
        self._descriptions: dict[str, dict[str, Any]] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._classes: dict[str, type] = {}
        self._build_eager(exp.instruments)

    # ------------------------- construction -------------------------

    @classmethod
    def from_config_dir(cls, config_dir: str) -> "InstrumentRegistry":
        """Build a lazy registry hosting the whole ``config/instruments`` tree.

        Runs the same hash repair the wizard runs on load, so the server's
        ``inst://`` paths agree with the keys the wizard shows.
        """
        from lab_wizard.lib.utilities.config_io import (
            load_instruments,
            validate_and_repair_hashes,
        )

        instruments = load_instruments(config_dir)
        repaired, _changed = validate_and_repair_hashes(instruments)
        return cls.from_instruments(repaired)

    @classmethod
    def from_instruments(cls, instruments: dict[str, Any]) -> "InstrumentRegistry":
        """Build a lazy registry from a ``{key: ParentParams}`` dict."""
        self = cls.__new__(cls)
        self._index = {}
        self._attribute_index = {}
        self._descriptions = {}
        self._factories = {}
        self._classes = {}
        self._build_lazy(instruments)
        return self

    # ------------------------- eager build -------------------------

    def _build_eager(self, instruments: dict[str, Any]) -> None:
        for root_key, root_params in instruments.items():
            if not hasattr(root_params, "create_inst"):
                raise TypeError(
                    f"Root params {type(root_params).__name__!s} at key {root_key!r} "
                    "does not implement create_inst(); top-level instruments must "
                    "inherit CanInstantiate."
                )
            inst = root_params.create_inst()
            path = f"{PATH_PREFIX}{root_key}"
            self._register_live(path, inst, root_params)
            self._walk_eager(inst, root_params, path)

    def _walk_eager(self, inst: Any, params: Any, parent_path: str) -> None:
        children_params = getattr(params, "children", None)
        if isinstance(children_params, dict):
            for child_key, child_params in children_params.items():
                child_inst = inst.make_child(child_key)
                child_path = f"{parent_path}/{child_key}"
                self._register_live(child_path, child_inst, child_params)
                self._walk_eager(child_inst, child_params, child_path)

        channels = getattr(inst, "channels", None)
        channel_params = getattr(params, "channels", None)
        if isinstance(channels, list):
            for i, channel in enumerate(channels):
                ch_path = f"{parent_path}/channel/{i}"
                ch_params = (
                    channel_params[i]
                    if isinstance(channel_params, list) and i < len(channel_params)
                    else None
                )
                self._register_live(ch_path, channel, ch_params)

    def _register_live(self, path: str, obj: Any, params: Any | None) -> None:
        if path in self._index:
            raise ValueError(f"Duplicate path {path!r}")
        self._index[path] = obj
        self._classes[path] = type(obj)
        self._descriptions[path] = {
            "behavior_abc": _behavior_abc_name(obj),
            "type_hint": type(obj).__name__,
        }
        self._index_attribute(path, params)

    # ------------------------- lazy build -------------------------

    def _build_lazy(self, instruments: dict[str, Any]) -> None:
        for root_key, root_params in instruments.items():
            if not hasattr(root_params, "create_inst"):
                raise TypeError(
                    f"Root params {type(root_params).__name__!s} at key {root_key!r} "
                    "does not implement create_inst(); top-level instruments must "
                    "inherit CanInstantiate."
                )
            path = f"{PATH_PREFIX}{root_key}"
            params = root_params  # bind for the closure
            self._register_lazy(
                path,
                params,
                factory=lambda params=params: params.create_inst(),
            )
            self._walk_lazy(root_params, path)

    def _walk_lazy(self, params: Any, parent_path: str) -> None:
        children_params = getattr(params, "children", None)
        if isinstance(children_params, dict):
            for child_key, child_params in children_params.items():
                child_path = f"{parent_path}/{child_key}"
                self._register_lazy(
                    child_path,
                    child_params,
                    factory=lambda pp=parent_path, ck=child_key: self.resolve(
                        pp
                    ).make_child(ck),
                )
                self._walk_lazy(child_params, child_path)

        channel_params = getattr(params, "channels", None)
        if isinstance(channel_params, list) and channel_params:
            chan_cls = _channel_class(getattr(params, "inst", None))
            chan_abc = _behavior_abc_for_class(chan_cls)
            chan_type_hint = chan_cls.__name__ if chan_cls is not None else None
            for i, ch_params in enumerate(channel_params):
                ch_path = f"{parent_path}/channel/{i}"
                self._register_lazy(
                    ch_path,
                    ch_params,
                    factory=lambda pp=parent_path, idx=i: self.resolve(pp).channels[
                        idx
                    ],
                    behavior_abc=chan_abc,
                    type_hint=chan_type_hint,
                    node_class=chan_cls,
                )

    def _register_lazy(
        self,
        path: str,
        params: Any | None,
        *,
        factory: Callable[[], Any],
        behavior_abc: Optional[str] = None,
        type_hint: Optional[str] = None,
        node_class: Optional[type] = None,
    ) -> None:
        if path in self._factories or path in self._index:
            raise ValueError(f"Duplicate path {path!r}")
        self._factories[path] = factory
        if node_class is None and behavior_abc is None and type_hint is None:
            inst_cls = getattr(params, "inst", None) if params is not None else None
            if isinstance(inst_cls, type):
                node_class = inst_cls
                behavior_abc = _behavior_abc_for_class(inst_cls)
                type_hint = inst_cls.__name__
        if isinstance(node_class, type):
            self._classes[path] = node_class
        self._descriptions[path] = {
            "behavior_abc": behavior_abc,
            "type_hint": type_hint,
        }
        self._index_attribute(path, params)

    # ------------------------- shared helpers -------------------------

    def _index_attribute(self, path: str, params: Any | None) -> None:
        if params is None:
            return
        attr_name = getattr(params, "attribute_name", None)
        if isinstance(attr_name, str) and attr_name:
            if attr_name in self._attribute_index:
                raise ValueError(
                    f"Duplicate attribute_name {attr_name!r} "
                    f"(at {self._attribute_index[attr_name]} and {path})"
                )
            self._attribute_index[attr_name] = path

    # ------------------------- query API -------------------------

    def resolve(self, path: str) -> Any:
        if path in self._index:
            return self._index[path]
        factory = getattr(self, "_factories", {}).get(path)
        if factory is not None:
            obj = factory()
            self._index[path] = obj
            return obj
        raise KeyError(f"No instrument registered at path {path!r}")

    def list_paths(self) -> list[str]:
        paths = set(self._index) | set(getattr(self, "_factories", {}))
        return sorted(paths)

    def list_attributes(self) -> dict[str, str]:
        return dict(self._attribute_index)

    def resolve_attribute_path(self, name: str) -> str:
        """Map an ``attribute_name`` to its ``inst://`` path (no instantiation)."""
        if name not in self._attribute_index:
            raise KeyError(f"No instrument with attribute_name {name!r}")
        return self._attribute_index[name]

    def describe_path(self, path: str) -> dict[str, Any]:
        """Return metadata about ``path``: behavior_abc, type_hint.

        Uses statically-derived metadata when available (no instantiation);
        falls back to live introspection for paths registered as live objects
        without a cached description.
        """
        desc = getattr(self, "_descriptions", {}).get(path)
        if desc is not None:
            return {"path": path, **desc}
        obj = self.resolve(path)
        return {
            "path": path,
            "behavior_abc": _behavior_abc_name(obj),
            "type_hint": type(obj).__name__,
        }

    def instrument_class(self, path: str) -> type | None:
        """Instrument class registered at ``path`` (statically known, no init)."""
        return getattr(self, "_classes", {}).get(path)

    def describe_attribute(self, name: str) -> dict[str, Any]:
        """Return metadata for the leaf named ``name`` (via params.attribute_name)."""
        path = self.resolve_attribute_path(name)
        info = self.describe_path(path)
        info["attribute_name"] = name
        return info

    def list_descriptions(self) -> list[dict[str, Any]]:
        """One entry per named attribute, with metadata. Used by the wizard."""
        return [self.describe_attribute(n) for n in sorted(self._attribute_index)]
