"""Network wire layer for the lab_wizard server.

Built on pyleco's wire primitives:
    - ``pyleco.core.message.Message`` for multipart framing (version, receiver,
      sender, header, payload). The header carries a conversation_id, message_id,
      and a one-byte message_type.
    - ``pyleco.json_utils.rpc_server.RPCServer`` for JSON-RPC 2.0 method
      dispatch and structured error responses.

We deliberately skip pyleco's ``MessageHandler`` / ``Coordinator``: that layer is
a Coordinator-client topology (DEALER connecting out to a central router) and
adds infrastructure we don't need for a single-server deployment. Instead we
bind a ZMQ ROUTER socket directly. The wire format is identical to pyleco,
so a future migration to a Coordinator-based topology is an additive change.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import zmq

from pyleco.core.message import Message
from pyleco.core.serialization import MessageTypes
from pyleco.json_utils.errors import JSONRPCError
from pyleco.json_utils.json_objects import JsonRpcError
from pyleco.json_utils.rpc_server import RPCServer

from lab_wizard.lib.instruments.general.state_effects import collect_query_methods
from lab_wizard.lib.server.claims import Claim, ClaimConflict, ClaimTable
from lab_wizard.lib.server.events import EventLog
from lab_wizard.lib.server.peer import (
    LocalOnlyError,
    Peer,
    current_peer,
    require_local,
    reset_current_peer,
    set_current_peer,
)
from lab_wizard.lib.server.permissions import PermissionGate
from lab_wizard.lib.server.registry import PATH_PREFIX, InstrumentRegistry, root_path


SERVER_NAME = b"lab_wizard_server"

# JSON-RPC server-error range (-32000..-32099). -32001 = permission denied,
# -32002 = the path is claimed by another run (or the caller's claim is gone).
PERMISSION_DENIED_CODE = -32001
CLAIM_DENIED_CODE = -32002

DEFAULT_CLAIM_TTL_S = 30.0
MAX_CLAIM_TTL_S = 3600.0

# Methods that may block on hardware. They run on their own worker pool so that
# fast requests — above all claim renewals — are never stuck behind a long
# ``count()``, or a claim would expire while its holder was only waiting.
HARDWARE_RPC = frozenset({"call", "discover", "release", "tree_add", "tree_remove", "tree_reset"})

log = logging.getLogger(__name__)


class WireServer:
    """ZMQ ROUTER socket + pyleco Message framing + JSON-RPC dispatch.

    Phase 1 surface:
        call(path, method, args=None, kwargs=None) -> result
        list_paths() -> list[str]
        list_attributes() -> dict[str, str]   # attribute_name -> path
    """

    def __init__(
        self,
        bind: str | list[str],
        registry: InstrumentRegistry,
        gate: Optional[PermissionGate] = None,
        config_dir: Optional[str] = None,
        events: Optional[EventLog] = None,
        claims: Optional[ClaimTable] = None,
        hardware_workers: int = 16,
        control_workers: int = 4,
    ) -> None:
        self._binds = [bind] if isinstance(bind, str) else list(bind)
        if not self._binds:
            raise ValueError("WireServer needs at least one bind address")
        self._ipc_binds = [b for b in self._binds if b.startswith("ipc://")]
        self._tcp_binds = [b for b in self._binds if not b.startswith("ipc://")]
        self._registry = registry
        self._gate = gate
        # Present only in config-dir hosting mode. Single-project hosting has no
        # editable tree, so tree_get() refuses rather than inventing one.
        self._config_dir = config_dir
        # Records who changed what. Kept beside the config so a change made from
        # another workspace is explainable later.
        self._events = events or EventLog(
            Path(config_dir) / "server" / "events.jsonl" if config_dir else None
        )

        self._claims = claims or ClaimTable()
        # Serializes check -> dispatch -> record for calls a permission rule
        # involves (see PermissionGate.involves). Always taken *inside* a
        # transport lock and never held while taking one, so it cannot deadlock.
        self._gate_lock = threading.Lock()
        self._hardware_workers = hardware_workers
        self._control_workers = control_workers
        self._hardware_pool: Optional[ThreadPoolExecutor] = None
        self._reply_addr: Optional[str] = None
        self._reply_local = threading.local()
        self._reply_sockets: list[zmq.Socket] = []
        self._reply_sockets_lock = threading.Lock()

        self._rpc = RPCServer(title="lab_wizard_server")
        self._rpc.method()(self.call)
        self._rpc.method()(self.list_paths)
        self._rpc.method()(self.list_held)
        self._rpc.method()(self.discover)
        self._rpc.method()(self.release)
        self._rpc.method()(self.tree_get)
        self._rpc.method()(self.schema_get)
        self._rpc.method()(self.config_status)
        self._rpc.method()(self.tree_add)
        self._rpc.method()(self.tree_remove)
        self._rpc.method()(self.tree_reset)
        self._rpc.method()(self.events_recent)
        self._rpc.method()(self.list_attributes)
        self._rpc.method()(self.describe_path)
        self._rpc.method()(self.describe_attribute)
        self._rpc.method()(self.list_descriptions)
        self._rpc.method()(self.claim_acquire)
        self._rpc.method()(self.claim_renew)
        self._rpc.method()(self.claim_release)
        self._rpc.method()(self.claim_list)
        self._rpc.method()(self.claim_force_release)

        self._ctx = zmq.Context.instance()
        self._sockets: dict[str, zmq.Socket] = {}
        self._running = False

    # ------------------------- RPC methods -------------------------

    def call(
        self,
        path: str,
        method: str,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
        token: Optional[str] = None,
    ) -> Any:
        """Invoke ``method`` on the object at ``path`` and return the result.

        ``token`` is the caller's run claim, if it holds one (see
        :mod:`lab_wizard.lib.server.claims`). A write to a claimed path needs a
        claim covering it; a query needs nothing. If a permission gate is
        configured, the call is checked before dispatch (denied calls raise a
        structured -32001 error) and recorded after.
        """
        pos = args or []
        kw = kwargs or {}

        # Held for the whole transaction — resolve (which may open the
        # transport), the call itself, and the state record. A driver method is
        # often several writes against connection-global state, so releasing
        # between them would let another caller interleave mid-query. Calls on
        # different roots take different locks and still run in parallel.
        with self._hold_transport(path) as registry:
            # Checked inside the lock, so a claim granted while this call waited
            # is honoured. A write already past this point when a claim lands
            # started before the claim existed; the new holder's baseline call
            # queues behind it on the same lock.
            self._check_claim(registry, path, method, token)
            # A rack claimed by another process must not be opened here, or the
            # claim would mean nothing. Checked before resolve, since resolve is
            # what actually opens the transport.
            self._refuse_if_leased(path)
            target = registry.resolve(path)

            gated = self._gate is not None and self._gate.involves(
                path, method, registry.instrument_class(path) or type(target)
            )
            with self._gate_lock if gated else _no_lock():
                if self._gate is not None:
                    denial = self._gate.check(path, method, pos, kw)
                    if denial is not None:
                        raise JSONRPCError(
                            JsonRpcError(
                                code=PERMISSION_DENIED_CODE,
                                message=denial.message,
                                data={
                                    "rule_id": denial.rule_id,
                                    "blocking_state": denial.blocking_state,
                                },
                            )
                        )

                if not hasattr(target, method):
                    raise AttributeError(
                        f"{type(target).__name__} at {path!r} has no method {method!r}"
                    )
                fn = getattr(target, method)
                if not callable(fn):
                    raise TypeError(f"{type(target).__name__}.{method} is not callable")
                result = fn(*pos, **kw)

                if self._gate is not None:
                    self._gate.record(path, target, method, pos, kw, result)
                return result

    def list_paths(self) -> list[str]:
        return self._registry.list_paths()

    def list_held(self) -> dict[str, Any]:
        """What this server has actually opened, and on what terms.

        Answers "may I open this hardware myself?" for a local program. The
        server declares every configured path but holds only what a call has
        resolved, so this is deliberately narrower than ``list_paths``.
        ``exclusive_roots`` is included because a root that is merely
        *configured* here will conflict later even if nothing holds it yet.
        """
        return {
            "held_paths": self._registry.list_held(),
            "held_roots": sorted(self._registry.held_roots()),
            "exclusive_roots": self._registry.exclusive_roots(),
        }

    def discover(
        self,
        type: str,
        action: str,
        params: Optional[dict[str, Any]] = None,
        parent_chain: Optional[list[dict[str, Any]]] = None,
    ) -> dict[str, Any]:
        """Run an instrument's discovery action here, where the hardware is.

        Discovery scans a bus — it must open the transport. The wizard used to
        do that in its own process, which meant two processes owning one serial
        handle whenever the server was up, and left the permission gate blind to
        whatever discovery touched. Running it here keeps a single owner.

        The parent chain is resolved through the registry so discovery reuses
        the *already open* connection rather than building a second one, and it
        is done under the root's transport lock so a scan cannot interleave with
        an ordinary call on the same bus.
        """
        from lab_wizard.lib.utilities.resource_catalog import load_params_class

        cls = load_params_class(type)
        actions = {a.name: a for a in cls.discovery_actions()}
        if action not in actions:
            raise ValueError(f"Type {type!r} has no discovery action {action!r}")

        chain = parent_chain or []
        if not chain:
            return actions[action].run(params or {}, parent=None).model_dump()

        parent_path = PATH_PREFIX + "/".join(step["key"] for step in chain)
        root = root_path(parent_path)
        was_held = root in self._registry.held_roots()
        with self._hold_transport(parent_path) as registry:
            parent_inst = registry.resolve(parent_path)
            try:
                return actions[action].run(
                    params or {}, parent=parent_inst
                ).model_dump()
            finally:
                # Discovery may have opened this rack only to perform the scan.
                # Keeping that transient handle made the immediately following
                # tree_add fail its held-rack safety check. Preserve a rack that
                # was already live, but hand back one opened solely by discovery.
                if not was_held:
                    registry.release(root)

    def release(self, path: str) -> list[str]:
        """Disconnect and evict ``path`` and everything under it.

        Lets an operator hand a rack back without stopping the whole server.
        Refused while a run holds a claim touching it: disconnecting hardware a
        run is driving would fail that run halfway through.
        """
        with self._hold_transport(path) as registry:
            touching = self._claims.touching(path)
            if touching:
                raise ValueError(
                    f"Cannot release {path}: {touching[0].holder} holds a claim on "
                    f"{', '.join(touching[0].units)}. Wait for the run to finish, or "
                    "force-release the claim first."
                )
            return registry.release(path)

    # ------------------------- tree (read-only) -------------------------

    def tree_get(self) -> dict[str, Any]:
        """The instrument tree this server hosts, shaped for the wizard's UI.

        Read-only, and **same-machine only**. The tree is the surface you edit
        from: its hash keys, parent chains and per-root transport facts are what
        ``tree_add`` / ``tree_remove`` operate on, and a remote peer may not
        call those. Serving it over tcp would invite a client to render an
        editable-looking hierarchy the wire then refuses to act on — so the
        restriction lives here rather than in whichever page happens to ask.

        A remote peer is not left blind: ``list_descriptions`` gives it one
        entry per named leaf, which is exactly what read + call needs.

        Deliberately the same shape ``/api/manage-instruments`` already returns,
        so the existing tree components render another workspace's tree with no
        change beyond where the data came from. Carries transport facts per root
        so a node can show whether it is exclusive or shared and whether it is
        currently held — the difference between "this rack is busy" and "this
        rack is configured".
        """
        from lab_wizard.lib.utilities.config_io import get_configured_tree

        require_local("Reading the instrument tree")
        config_dir = self._config_dir
        if config_dir is None:
            raise ValueError(
                "This server hosts a single project rather than a config tree, "
                "so it has no editable instrument tree to serve."
            )
        return {
            "tree": get_configured_tree(config_dir),
            "roots": {
                root: self._registry.transport_for(root)
                for root in sorted(
                    {root_path(p) for p in self._registry.list_paths()}
                )
            },
            "held_roots": sorted(self._registry.held_roots()),
            "config_dir": str(config_dir),
        }

    def config_status(self) -> dict[str, Any]:
        """Whether the config on disk still matches what this server loaded.

        The tree is snapshotted at boot. A ``git pull``, a text editor, or the
        wizard writing YAML all move the file underneath a running server, and
        without this the wizard would show server state, the file would say
        something else, and ``git diff`` would show changes nobody made through
        the UI.

        Compared by the set of registered paths rather than file mtimes, so a
        reformat or comment edit is correctly reported as no change.
        """
        config_dir = self._config_dir
        if config_dir is None:
            return {"tracks_config": False}

        try:
            fresh = InstrumentRegistry.from_config_dir(config_dir)
        except Exception as exc:  # noqa: BLE001 - a broken edit is a real answer
            return {
                "tracks_config": True,
                "diverged": True,
                "error": str(exc),
                "detail": "The config on disk no longer loads.",
            }

        loaded = set(self._registry.list_paths())
        on_disk = set(fresh.list_paths())
        added = sorted(on_disk - loaded)
        removed = sorted(loaded - on_disk)
        return {
            "tracks_config": True,
            "diverged": bool(added or removed),
            "added_paths": added,
            "removed_paths": removed,
            # Restarting is what picks the change up; held hardware is released
            # cleanly on the way down.
            "resolution": "restart" if (added or removed) else None,
        }

    # ------------------------- tree (writes) -------------------------

    def tree_add(self, chain: list[dict[str, Any]]) -> dict[str, Any]:
        """Add an instrument (with any parent chain) to this server's config.

        Same-machine callers only. The generated ``attribute_name`` is assigned
        *here*, against this server's whole tree, so it is unique by
        construction — a remote workspace never picks the name and so cannot
        collide with one it cannot see.
        """
        peer = require_local("Adding instruments")
        config_dir = self._require_config_dir()

        from lab_wizard.lib.utilities.config_io import add_instrument_chain

        with self._tree_write_lock():
            for step in chain:
                self._refuse_if_held(step.get("key"), "reconfigure")
                self._refuse_if_claimed(step.get("key"), "reconfigure")
            result = add_instrument_chain(config_dir, chain)
            self._reload_tree("tree_add")
        self._events.record(
            "tree.add",
            "Added "
            + ", ".join(f"{s.get('type')}" for s in chain if s.get("type"))
            + " to the instrument tree",
            actor=peer.describe(),
            keys=result.get("saved_keys"),
        )
        return result

    def tree_remove(self, type: str, key: str) -> dict[str, Any]:
        """Remove an instrument and its children. Same-machine callers only."""
        peer = require_local("Removing instruments")
        config_dir = self._require_config_dir()

        from lab_wizard.lib.utilities.config_io import remove_instrument

        with self._tree_write_lock():
            self._refuse_if_held(key, "remove")
            self._refuse_if_claimed(key, "remove")
            result = remove_instrument(config_dir, type, key)
            self._reload_tree("tree_remove")
        self._events.record(
            "tree.remove",
            f"Removed {type} ({key}) from the instrument tree",
            actor=peer.describe(),
            type=type,
            key=key,
        )
        return result

    def tree_reset(self, type: str, key: str) -> dict[str, Any]:
        """Reset an instrument to defaults, preserving children."""
        peer = require_local("Resetting instruments")
        config_dir = self._require_config_dir()

        from lab_wizard.lib.utilities.config_io import reinitialize_instrument

        with self._tree_write_lock():
            self._refuse_if_held(key, "reset")
            self._refuse_if_claimed(key, "reset")
            result = reinitialize_instrument(config_dir, type, key)
            self._reload_tree("tree_reset")
        self._events.record(
            "tree.reset",
            f"Reset {type} ({key}) to defaults",
            actor=peer.describe(),
            type=type,
            key=key,
        )
        return result

    def events_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent notable events on this server, newest first."""
        return [dict(e) for e in self._events.recent(limit)]

    # ------------------------- write helpers -------------------------

    def _require_config_dir(self) -> str:
        if self._config_dir is None:
            raise ValueError(
                "This server hosts a single project rather than a config tree, "
                "so its instrument tree cannot be edited."
            )
        return self._config_dir

    def _roots_containing(self, key: str) -> set[str]:
        """Root paths whose subtree contains a node with this hash ``key``.

        A chain step or a removal target may name a *child*, whose key is a
        segment somewhere inside a path rather than a root. Reconfiguring it
        still rebuilds the index under its root, so the root is what must be
        free — resolving the key to its owning root is the difference between
        the guard firing and silently passing.
        """
        owners: set[str] = set()
        for path in self._registry.list_paths():
            body = path[len(PATH_PREFIX):]
            if key in body.split("/"):
                owners.add(root_path(path))
        return owners

    def _refuse_if_held(self, key: Optional[str], verb: str) -> None:
        """Refuse to reconfigure a rack whose hardware is currently open.

        Rebuilding the index under a live instrument goes wrong three ways: the
        old object keeps the serial handle with nothing able to reach it, the
        next call tries to open a port that is still held, and an in-flight call
        holds a lock from the old lock table while new calls take one from the
        new table — two locks for one physical bus, which is the Prologix
        desync. Requiring the rack to be free removes all three, and the fix is
        a Release away.

        The key is resolved to whichever roots contain it. Testing
        ``inst://<key>`` directly only ever matched a top-level ``use_existing``
        step: a ``create_new`` step carries a raw hardware address and a child
        step carries a child hash, so neither formed a real root path and the
        check passed for exactly the edits most likely to disturb live hardware.
        """
        if not key:
            return
        held = self._registry.held_roots()
        blocking = sorted(self._roots_containing(key) & held)
        if blocking:
            raise ValueError(
                f"Cannot {verb} {', '.join(blocking)} while its hardware is open. "
                "Release it first (Hardware & Servers → Release), then try again."
            )

    def _refuse_if_claimed(self, key: Optional[str], verb: str) -> None:
        """Refuse to reconfigure a rack a run holds a claim on.

        Separate from ``_refuse_if_held``: a run can hold a claim on a rack that
        happens to have no open handle at this instant, and rebuilding the tree
        under it would move the paths its claim names.
        """
        if not key:
            return
        for root in sorted(self._roots_containing(key)):
            touching = self._claims.touching(root)
            if touching:
                raise ValueError(
                    f"Cannot {verb} {root} while {touching[0].holder} holds a claim "
                    f"on {', '.join(touching[0].units)}."
                )

    @contextmanager
    def _tree_write_lock(self) -> Iterator[None]:
        """Hold every transport lock across a config change.

        ``_refuse_if_held`` establishes that no rack is *currently* open, but on
        its own that is a check-then-act: a call could resolve a path in the
        window before the index is replaced. Holding the locks makes the check
        and the swap one step.

        Every root, not just the edited one — the reload replaces the whole lock
        table, so a concurrent call on an *unrelated* root would end up holding a
        lock from the old table while later calls take one from the new. Two
        locks for one bus is the desync this exists to prevent. Tree writes are
        rare and calls take exactly one lock, so acquiring in sorted order cannot
        deadlock.
        """
        roots = sorted({root_path(p) for p in self._registry.list_paths()})
        with ExitStack() as stack:
            for root in roots:
                stack.enter_context(self._registry.transport_lock(root))
            yield

    def _refuse_if_leased(self, path: str) -> None:
        """Decline to open hardware another process has claimed.

        Only applies to exclusive transports and only to paths not already open:
        a rack we are holding was ours before the claim existed, and a shared
        transport is not contended by definition.
        """
        if path in self._registry.list_held():
            return
        info = self._registry.transport_for(path)
        if info.get("transport_sharing") == "shared":
            return
        key = info.get("transport_key")
        if not key:
            return

        from lab_wizard.lib.client.leases import holder_of

        holder = holder_of(key)
        if holder is None or holder.get("pid") == os.getpid():
            return
        raise ValueError(
            f"{key} is claimed by {holder.get('owner', 'another process')} "
            f"(pid {holder.get('pid')}). This server will not open it until the "
            "claim is released."
        )

    def _reload_tree(self, reason: str) -> None:
        """Rebuild the registry from disk after a config change.

        Safe only because every affected root was required to be free above: with
        no live objects under them there is no handle to strand and no in-flight
        call holding a lock we are about to replace. Live objects for *other*
        roots are carried over so an unrelated rack is not disturbed by an edit
        elsewhere.
        """
        config_dir = self._require_config_dir()
        live = dict(self._registry.list_held_objects())
        fresh = InstrumentRegistry.from_config_dir(config_dir)
        fresh.adopt_live(live)
        self._registry = fresh
        log.info("Reloaded instrument tree after %s", reason)

    def schema_get(self) -> dict[str, Any]:
        """Vocabulary for rendering and editing this server's tree.

        Served by the *server* on purpose. Which instrument classes exist, what
        fields they take, what discovery actions they offer and which state keys
        and methods a rule may reference all depend on the lab_wizard build
        running here — which can differ from the client's. The server sends data
        and schema; the client renders it. That is what stops a client offering
        an instrument the server cannot instantiate.
        """
        from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata

        return {
            "instrument_metadata": get_instrument_metadata(),
            "permission_vocabulary": self._permission_vocabulary(),
        }

    def _permission_vocabulary(self) -> list[dict[str, Any]]:
        """Per-path state keys and methods a rule may reference here."""
        from lab_wizard.wizard.backend.permissions_api import (
            _addressable_instruments,
        )

        return _addressable_instruments(self._registry)

    # ------------------------- claims -------------------------

    def claim_acquire(
        self,
        paths: Optional[list[str]] = None,
        attributes: Optional[list[str]] = None,
        holder: str = "",
        ttl_s: float = DEFAULT_CLAIM_TTL_S,
    ) -> dict[str, Any]:
        """Claim the instruments a run will drive, all or none.

        Open to remote peers as well as local ones: a measurement on another
        machine must be able to claim. Each path is widened to the unit its
        instrument can be claimed as (``registry.claim_unit_for``), so claiming
        one SIM970 channel claims the SIM970. The returned token goes on every
        subsequent ``call``.
        """
        if not 0 < ttl_s <= MAX_CLAIM_TTL_S:
            raise ValueError(f"ttl_s must be between 0 and {MAX_CLAIM_TTL_S:g} seconds")
        self._reap_expired()
        registry = self._registry
        wanted = list(paths or [])
        for name in attributes or []:
            wanted.append(registry.resolve_attribute_path(name))
        known = set(registry.list_paths())
        for path in wanted:
            if path not in known:
                raise ValueError(f"No instrument at {path!r} on this server")

        peer = current_peer()
        actor = peer.describe() if peer else "in-process"
        units = [registry.claim_unit_for(path) for path in wanted]
        label = holder or actor
        try:
            claim = self._claims.acquire(units, holder=label, peer=actor, ttl_s=ttl_s)
        except ClaimConflict as exc:
            self._events.record(
                "claim.denied",
                f"Refused {label} a claim on {exc.unit}: {exc}",
                actor=actor,
                unit=exc.unit,
                held_by=exc.holder.holder,
            )
            raise JSONRPCError(
                JsonRpcError(
                    code=CLAIM_DENIED_CODE,
                    message=str(exc),
                    data={"unit": exc.unit, "held_by": exc.holder.to_wire(0.0)},
                )
            ) from exc
        self._events.record(
            "claim.acquire",
            f"{label} claimed {', '.join(claim.units)}",
            actor=actor,
            units=list(claim.units),
            ttl_s=claim.ttl_s,
        )
        return {"token": claim.token, "units": list(claim.units), "ttl_s": claim.ttl_s}

    def claim_renew(self, token: str) -> dict[str, Any]:
        """Keep a claim alive for another TTL."""
        self._reap_expired()
        try:
            claim = self._claims.renew(token)
        except KeyError:
            raise JSONRPCError(
                JsonRpcError(
                    code=CLAIM_DENIED_CODE,
                    message=(
                        "This claim is no longer held — it expired, was released, "
                        "or the server restarted."
                    ),
                    data={"token_known": False},
                )
            ) from None
        return {"units": list(claim.units), "ttl_s": claim.ttl_s}

    def claim_release(self, token: str) -> dict[str, Any]:
        """Release a claim. Its units are reset to baseline, then freed."""
        claim = self._claims.release(token)
        if claim is None:
            return {"released": []}
        peer = current_peer()
        self._after_release([claim], "release", peer.describe() if peer else "in-process")
        return {"released": list(claim.units)}

    def claim_list(self) -> list[dict[str, Any]]:
        """Live and restoring claims, oldest first. Tokens are never listed."""
        self._reap_expired()
        return self._claims.snapshot()

    def claim_force_release(self, unit: str) -> dict[str, Any]:
        """Release every claim touching ``unit``. Same-machine callers only.

        For a run that is stuck, or whose client vanished and whose TTL is long.
        """
        peer = require_local("Force-releasing a claim")
        released: list[Claim] = []
        for claim in self._claims.touching(unit):
            if not claim.restoring and self._claims.release(claim.token) is not None:
                released.append(claim)
        self._after_release(released, "force_release", peer.describe())
        return {"released": [u for c in released for u in c.units]}

    def _is_query(self, registry: InstrumentRegistry, path: str, method: str) -> bool:
        """Whether ``method`` is a declared pure read on the class at ``path``.

        Read from the class, so a write can be refused before anything is
        resolved or opened. An unknown class counts as a write: the allowlist
        fails closed.
        """
        cls = registry.instrument_class(path)
        if cls is None and path in registry.list_held():
            cls = type(registry.resolve(path))
        return cls is not None and method in collect_query_methods(cls)

    def _check_claim(
        self, registry: InstrumentRegistry, path: str, method: str, token: Optional[str]
    ) -> None:
        """Enforce the claim rules for one call. See :mod:`...server.claims`."""
        self._reap_expired()
        mine = self._claims.live(token) if token else None
        if token and mine is None:
            # A run that believes it holds a claim must not carry on unowned.
            raise JSONRPCError(
                JsonRpcError(
                    code=CLAIM_DENIED_CODE,
                    message=(
                        "This run's claim is no longer held — it expired, was "
                        "released, or the server restarted. Stopping rather than "
                        "driving hardware without it."
                    ),
                    data={"token_known": False, "path": path},
                )
            )
        if self._is_query(registry, path, method):
            return
        touching = self._claims.touching(path)
        if not touching:
            return
        if mine is not None and mine.covers(path):
            return
        other = next((c for c in touching if c is not mine), None)
        if other is None:
            message = (
                f"This run's claim covers {', '.join(mine.units) if mine else '-'} "
                f"but not {path}, which holds state shared with it. Claim {path} "
                "itself to change it."
            )
            holder = mine.to_wire(0.0) if mine else None
        else:
            doing = "is being reset to baseline after" if other.restoring else "is claimed by"
            message = f"{path} {doing} {other.holder} ({', '.join(other.units)})."
            holder = other.to_wire(0.0)
        raise JSONRPCError(
            JsonRpcError(
                code=CLAIM_DENIED_CODE,
                message=message,
                data={"path": path, "method": method, "held_by": holder},
            )
        )

    def _reap_expired(self) -> None:
        expired = self._claims.pop_expired()
        if expired:
            self._after_release(expired, "expire", "server")

    def _after_release(self, claims: list[Claim], reason: str, actor: str) -> None:
        """Record the release and restore each claim's units to baseline."""
        for claim in claims:
            self._events.record(
                f"claim.{reason}",
                f"{claim.holder}'s claim on {', '.join(claim.units)} ended ({reason})",
                actor=actor,
                units=list(claim.units),
            )
            pool = self._hardware_pool
            if pool is not None:
                pool.submit(self._restore_baseline, claim)
            else:
                self._restore_baseline(claim)

    def _restore_baseline(self, claim: Claim) -> None:
        """Re-apply baseline under each released unit, then free the units.

        Only instruments already open are reset — releasing a claim must never
        open hardware. Shallowest first, so a counter's trigger is restored
        before its inputs. Failures are logged and recorded, and the units are
        freed regardless: a unit wedged forever is worse than one that may need
        attention.
        """
        try:
            for unit in claim.units:
                with self._hold_transport(unit) as registry:
                    held = sorted(
                        (p for p in registry.list_held() if p == unit or p.startswith(unit + "/")),
                        key=lambda p: p.count("/"),
                    )
                    for path in held:
                        target = registry.resolve(path)
                        if not callable(getattr(type(target), "apply_baseline", None)):
                            continue
                        try:
                            if target.apply_baseline() is False:
                                raise RuntimeError("apply_baseline reported failure")
                        except Exception as exc:  # noqa: BLE001 - every unit is attempted
                            log.warning("Baseline restore failed at %s: %s", path, exc)
                            self._events.record(
                                "claim.restore_failed",
                                f"Could not restore {path} to baseline after {claim.holder}: {exc}",
                                actor="server",
                                path=path,
                            )
        finally:
            self._claims.restored(claim.token)

    def list_attributes(self) -> dict[str, str]:
        return self._registry.list_attributes()

    def describe_path(self, path: str) -> dict[str, Any]:
        return self._registry.describe_path(path)

    def describe_attribute(self, name: str) -> dict[str, Any]:
        return self._registry.describe_attribute(name)

    def list_descriptions(self) -> list[dict[str, Any]]:
        return self._registry.list_descriptions()

    # ------------------------- Socket loop -------------------------

    def serve_forever(self) -> None:
        # One ROUTER *per transport*, not one socket bound to both. ZMQ does not
        # report which endpoint a message arrived on, and that fact is exactly
        # what decides authority: reaching the ipc socket requires filesystem
        # access to it, which only a process on this machine can have. Separate
        # sockets make "arrived locally" a structural property of the receive
        # path rather than something a client could claim.
        #
        # Requests are *processed* on worker threads, so one slow instrument no
        # longer blocks every other client. ZMQ sockets are not thread-safe, so
        # only this thread touches the ROUTERs: workers push finished replies to
        # an inproc socket that this loop polls and forwards.
        poller = zmq.Poller()
        for transport, endpoints in (("ipc", self._ipc_binds), ("tcp", self._tcp_binds)):
            if not endpoints:
                continue
            socket = self._ctx.socket(zmq.ROUTER)
            for endpoint in endpoints:
                socket.bind(endpoint)
                log.info("WireServer listening on %s (%s)", endpoint, transport)
            self._sockets[transport] = socket
            poller.register(socket, zmq.POLLIN)

        self._reply_addr = f"inproc://lab_wizard-replies-{uuid.uuid4().hex}"
        replies = self._ctx.socket(zmq.PULL)
        replies.bind(self._reply_addr)
        poller.register(replies, zmq.POLLIN)

        hardware = ThreadPoolExecutor(self._hardware_workers, thread_name_prefix="lw-hardware")
        control = ThreadPoolExecutor(self._control_workers, thread_name_prefix="lw-control")
        self._hardware_pool = hardware

        self._running = True
        try:
            while self._running:
                events = dict(poller.poll(timeout=200))
                for transport, socket in self._sockets.items():
                    if socket in events:
                        received = self._receive(socket, transport)
                        if received is not None:
                            method, job = received
                            (hardware if method in HARDWARE_RPC else control).submit(job)
                if replies in events:
                    self._forward_replies(replies)
                # Expiry is checked every tick, not only when a request happens
                # to arrive, so a vanished client's claim lapses on time.
                self._reap_expired()
        finally:
            # Stop taking work, let running requests finish, drop queued ones.
            self._hardware_pool = None
            control.shutdown(wait=True, cancel_futures=True)
            hardware.shutdown(wait=True, cancel_futures=True)
            self._forward_replies(replies)
            self._claims.drop_all()
            with self._reply_sockets_lock:
                for socket in self._reply_sockets:
                    socket.close(linger=0)
                self._reply_sockets.clear()
            replies.close(linger=0)
            for socket in self._sockets.values():
                socket.close(linger=0)
            self._sockets.clear()
            # Stop answering before letting hardware go, so no request can
            # resolve a path we are in the middle of disconnecting.
            released = self._registry.release_all()
            if released:
                log.info("Disconnected %d instrument(s) on shutdown", len(released))
            self._cleanup_ipc_endpoints()

    def stop(self) -> None:
        self._running = False

    @property
    def binds(self) -> list[str]:
        return list(self._binds)

    def _cleanup_ipc_endpoints(self) -> None:
        """Remove ipc:// socket files we created.

        ZMQ leaves the filesystem entry behind on close. A stale one makes the
        socket path look live to anything that probes for it by existence, so a
        client would try to dial a server that is gone.
        """
        for endpoint in self._binds:
            if not endpoint.startswith("ipc://"):
                continue
            try:
                Path(endpoint[len("ipc://"):]).unlink(missing_ok=True)
            except OSError as exc:
                log.debug("Could not remove socket file for %s: %s", endpoint, exc)

    # ------------------------- Internals -------------------------

    def _receive(self, socket: "zmq.Socket", transport: str):
        """Read one request; return ``(method, job)`` for a worker, or ``None``."""
        try:
            raw = socket.recv_multipart()
        except zmq.ZMQError as exc:
            log.warning("recv_multipart failed: %s", exc)
            return None

        # ROUTER prepends the peer identity; strip it for the LECO message.
        if len(raw) < 2:
            log.warning("Dropping short frame list: %r", raw)
            return None
        identity, frames = raw[0], raw[1:]

        try:
            msg = Message.from_frames(*frames)
        except Exception as exc:
            log.warning("Could not parse incoming Message: %s (frames=%r)", exc, frames)
            return None

        if msg.header_elements.message_type != MessageTypes.JSON:
            log.warning("Ignoring non-JSON message_type=%s", msg.header_elements.message_type)
            return None

        if not msg.payload:
            log.warning("Ignoring message with empty payload")
            return None

        request_bytes = msg.payload[0]
        try:
            method = json.loads(request_bytes).get("method")
        except (ValueError, AttributeError):
            method = None  # malformed: the RPC server will say so

        def job() -> None:
            reply = self._process(transport, identity, msg, request_bytes)
            if reply is not None:
                self._push_reply(transport, identity, reply)

        return method, job

    def _process(
        self, transport: str, identity: bytes, msg: Message, request_bytes: bytes
    ) -> Optional[list[bytes]]:
        """Dispatch one request on a worker thread; return the reply frames."""
        # Published for the duration of dispatch so RPC methods can consult the
        # caller without threading a parameter through every signature.
        # ContextVars are per thread, so concurrent workers do not see each
        # other's peer.
        peer = Peer(
            transport=transport,
            identity=identity.hex(),
            name=msg.sender.decode(errors="replace") if msg.sender else None,
        )
        token = set_current_peer(peer)
        try:
            response_str = self._rpc.process_request(request_bytes)
        finally:
            reset_current_peer(token)
        if response_str is None:
            # Notification — no response.
            return None

        try:
            response_obj = json.loads(response_str)
        except json.JSONDecodeError as exc:
            log.error("RPCServer returned non-JSON response: %s", exc)
            return None

        reply = Message(
            receiver=msg.sender or SERVER_NAME,
            sender=SERVER_NAME,
            data=response_obj,
            conversation_id=msg.conversation_id,
            message_type=MessageTypes.JSON,
        )
        return reply.to_frames()

    def _push_reply(self, transport: str, identity: bytes, frames: list[bytes]) -> None:
        """Hand a finished reply to the loop thread, which owns the ROUTERs."""
        socket = getattr(self._reply_local, "socket", None)
        if socket is None:
            if self._reply_addr is None:
                return
            socket = self._ctx.socket(zmq.PUSH)
            socket.setsockopt(zmq.LINGER, 0)
            socket.connect(self._reply_addr)
            self._reply_local.socket = socket
            with self._reply_sockets_lock:
                self._reply_sockets.append(socket)
        socket.send_multipart([transport.encode(), identity, *frames])

    def _forward_replies(self, replies: "zmq.Socket") -> None:
        while True:
            try:
                transport, identity, *frames = replies.recv_multipart(zmq.NOBLOCK)
            except zmq.Again:
                return
            socket = self._sockets.get(transport.decode())
            if socket is None:
                continue
            try:
                socket.send_multipart([identity, *frames])
            except zmq.ZMQError as exc:
                log.warning("send_multipart failed: %s", exc)

    @contextmanager
    def _hold_transport(self, path: str) -> Iterator[InstrumentRegistry]:
        """Take ``path``'s transport lock on the *current* registry.

        A tree write replaces the registry while holding every transport lock.
        A worker that picked up the old registry just before the swap would,
        once the lock frees, resolve paths in an index nobody uses any more —
        opening a handle nothing can reach. So after taking the lock, check the
        registry is still current, and start again if not.
        """
        while True:
            registry = self._registry
            lock = registry.transport_lock(path)
            lock.acquire()
            if registry is self._registry:
                break
            lock.release()
        try:
            yield registry
        finally:
            lock.release()


@contextmanager
def _no_lock() -> Iterator[None]:
    yield
