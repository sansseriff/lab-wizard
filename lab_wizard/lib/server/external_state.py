"""Keep the permission gate's state current for externally-owned hardware.

Some roots declare ``state_authority() == "subscribed"``: another process owns
them and reports changes. A DBay rack in GUI mode is the case that exists today
— its backend is a lab-link reactive server, so anyone with the GUI open can
move a channel, and a gate that only recorded its own writes would be
confidently wrong about live hardware.

This bridges the two. It subscribes to the authority, translates what it reports
into ``(inst:// path, state_key)`` pairs via the instrument layer's declarative
map, and writes them into the tracker.

Three details that matter more than they look:

* **Echoes are dropped.** Our own commands come back as patches too. Applying
  them is harmless but pointless; ignoring them by ``origin_client_id`` keeps
  the tracker's writes attributable.
* **Version gaps force a resync.** The authority numbers its updates. If one is
  missed, patch-derived state is no longer trustworthy, so the whole subtree is
  cleared and reseeded from a fresh snapshot rather than left subtly stale.
* **Resync clears before it seeds.** Anything absent from the new snapshot ends
  up unset — "unknown" — instead of retaining an old value that may no longer
  describe the hardware. This mirrors DBay's own ``_reset_transient_flags``,
  which forces ``activated`` off at startup because output state is genuinely
  unknown then. Failing to the safe belief beats failing to the last one.

Subscription is best-effort: if the authority cannot be reached the server still
serves. It logs loudly, though, because a gate running on inferred state for a
subscribed root is exactly the silent-wrongness this exists to prevent.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from lab_wizard.lib.instruments.dbay.state_sync import channel_states
from lab_wizard.lib.server.registry import InstrumentRegistry, root_path


logger = logging.getLogger(__name__)

__all__ = ["ExternalStateBridge", "slot_channel_paths"]


def slot_channel_paths(
    registry: InstrumentRegistry, root: str
) -> dict[tuple[int, int], str]:
    """``{(slot, channel_index): inst:// path}`` for a root's channels.

    Built from the *params* tree, so it needs no hardware: the authority reports
    by slot, the gate addresses by path, and the child's ``slot`` field is what
    joins them.
    """
    out: dict[tuple[int, int], str] = {}
    prefix = f"{root}/"
    for path in registry.list_paths():
        if not path.startswith(prefix) or "/channel/" not in path:
            continue
        child_path, _, index_part = path.rpartition("/channel/")
        try:
            channel_index = int(index_part)
        except ValueError:
            continue
        params = registry.params_for(child_path)
        slot = getattr(params, "slot", None)
        if slot is None:
            continue
        try:
            out[(int(slot), channel_index)] = path
        except (TypeError, ValueError):
            continue
    return out


class ExternalStateBridge:
    """Feeds one subscribed root's reported state into a StateTracker."""

    def __init__(
        self,
        registry: InstrumentRegistry,
        tracker: Any,
        root: str,
        *,
        client_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self._registry = registry
        self._tracker = tracker
        self._root = root
        self._client_factory = client_factory
        self._client: Any = None
        self._unsubscribe: list[Callable[[], None]] = []
        self._paths = slot_channel_paths(registry, root)
        self._version = -1
        self._lock = threading.Lock()

        # record() must stop guessing for these paths before any call arrives.
        for path in self._paths.values():
            tracker.mark_subscribed(path)

    # ------------------------- lifecycle -------------------------

    def start(self) -> bool:
        """Connect, seed from a snapshot, and subscribe. False if unavailable."""
        try:
            self._client = (
                self._client_factory() if self._client_factory else self._build_client()
            )
        except Exception:
            logger.warning(
                "Could not reach the state authority for %s; its permission "
                "state will be UNKNOWN rather than inferred. Rules referencing "
                "it will not see external changes.",
                self._root,
                exc_info=True,
            )
            return False

        self._resync("initial")
        try:
            self._unsubscribe.append(self._client.on_patch(self._handle_patch))
            self._unsubscribe.append(self._client.on_snapshot(self._handle_snapshot))
        except Exception:
            logger.warning("Could not subscribe to %s", self._root, exc_info=True)
            return False

        logger.info(
            "Subscribed to external state for %s (%d channel(s))",
            self._root,
            len(self._paths),
        )
        return True

    def stop(self) -> None:
        for unsubscribe in self._unsubscribe:
            try:
                unsubscribe()
            except Exception:  # noqa: BLE001 - shutdown is best-effort
                logger.debug("Unsubscribe failed for %s", self._root, exc_info=True)
        self._unsubscribe.clear()
        if self._client is not None and hasattr(self._client, "close"):
            try:
                self._client.close()
            except Exception:  # noqa: BLE001
                logger.debug("Closing authority client failed", exc_info=True)
        self._client = None

    def _build_client(self) -> Any:
        """Open the authority's client from the root's own params."""
        params = self._registry.params_for(self._root)
        factory = getattr(params, "state_authority_client", None)
        if callable(factory):
            return factory()
        raise RuntimeError(
            f"{type(params).__name__} declares state_authority()='subscribed' but "
            "provides no state_authority_client(); the server cannot subscribe."
        )

    # ------------------------- updates -------------------------

    def _current_version(self) -> int:
        version = getattr(self._client, "state_version", None)
        return version if isinstance(version, int) else -1

    def _resync(self, reason: str) -> None:
        """Clear this root's state and reseed from a full snapshot."""
        with self._lock:
            try:
                snapshot = self._client.snapshot()
            except Exception:
                logger.warning(
                    "Snapshot failed for %s (%s); state left unknown",
                    self._root,
                    reason,
                    exc_info=True,
                )
                return

            self._tracker.clear_external(self._paths.values())
            applied = self._apply(snapshot)
            self._version = self._current_version()
            logger.info(
                "Resynced %s (%s): %d state value(s) at version %s",
                self._root,
                reason,
                applied,
                self._version,
            )

    def _apply(self, snapshot: Any) -> int:
        applied = 0
        for state in channel_states(snapshot):
            path = self._paths.get((state.slot, state.channel_index))
            if path is None:
                # A channel the config does not reference. Nothing addresses it,
                # so nothing can rule on it.
                continue
            self._tracker.set_external(path, state.key, state.value)
            applied += 1
        return applied

    def _handle_patch(self, event: Any) -> None:
        try:
            if self._is_echo(event):
                return
            version = getattr(event, "version", None)
            if isinstance(version, int) and self._version >= 0:
                if version > self._version + 1:
                    # Missed at least one update; patch-derived state cannot be
                    # trusted, so rebuild rather than drift.
                    self._resync(f"version gap {self._version}->{version}")
                    return
                self._version = version
            # The client applies each patch to its own snapshot before calling
            # back, so re-reading it is enough — we never apply patch ops.
            with self._lock:
                self._apply(self._client.snapshot())
        except Exception:  # noqa: BLE001 - a callback must not kill the thread
            logger.warning("Failed to apply external state patch", exc_info=True)

    def _handle_snapshot(self, _event: Any) -> None:
        self._resync("authority resnapshot")

    def _is_echo(self, event: Any) -> bool:
        origin = getattr(event, "origin_client_id", None)
        mine = getattr(self._client, "client_id", None)
        return bool(origin) and origin == mine


def attach_external_state(
    registry: InstrumentRegistry, tracker: Any
) -> list[ExternalStateBridge]:
    """Subscribe every root whose state an external authority owns."""
    bridges: list[ExternalStateBridge] = []
    for path in registry.list_paths():
        root = root_path(path)
        if root != path:
            continue
        if registry.transport_for(root).get("state_authority") != "subscribed":
            continue
        bridge = ExternalStateBridge(registry, tracker, root)
        if bridge.start():
            bridges.append(bridge)
    return bridges
