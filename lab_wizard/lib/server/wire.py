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
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

import zmq

from pyleco.core.message import Message
from pyleco.core.serialization import MessageTypes
from pyleco.json_utils.errors import JSONRPCError
from pyleco.json_utils.json_objects import JsonRpcError
from pyleco.json_utils.rpc_server import RPCServer

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

# JSON-RPC server-error range (-32000..-32099). -32001 = permission denied.
PERMISSION_DENIED_CODE = -32001

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
    ) -> Any:
        """Invoke ``method`` on the object at ``path`` and return the result.

        If a permission gate is configured, the call is checked before dispatch
        (denied calls raise a structured -32001 error) and recorded after.
        """
        pos = args or []
        kw = kwargs or {}

        # Held for the whole transaction — resolve (which may open the
        # transport), the call itself, and the state record. A driver method is
        # often several writes against connection-global state, so releasing
        # between them would let another caller interleave mid-query. Calls on
        # different roots take different locks and still run in parallel.
        with self._registry.transport_lock(path):
            # A rack claimed by another process must not be opened here, or the
            # claim would mean nothing. Checked before resolve, since resolve is
            # what actually opens the transport.
            self._refuse_if_leased(path)
            target = self._registry.resolve(path)

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
        from lab_wizard.lib.utilities.params_discovery import load_params_class

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
        with self._registry.transport_lock(parent_path):
            parent_inst = self._registry.resolve(parent_path)
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
                    self._registry.release(root)

    def release(self, path: str) -> list[str]:
        """Disconnect and evict ``path`` and everything under it.

        Lets an operator hand a rack back without stopping the whole server.
        """
        with self._registry.transport_lock(path):
            return self._registry.release(path)

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
        from lab_wizard.lib.utilities.params_discovery import get_instrument_metadata

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

        self._running = True
        try:
            while self._running:
                events = dict(poller.poll(timeout=200))
                for transport, socket in self._sockets.items():
                    if socket in events:
                        self._handle_one(socket, transport)
        finally:
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

    def _handle_one(self, socket: "zmq.Socket", transport: str) -> None:
        try:
            raw = socket.recv_multipart()
        except zmq.ZMQError as exc:
            log.warning("recv_multipart failed: %s", exc)
            return

        # ROUTER prepends the peer identity; strip it for the LECO message.
        if len(raw) < 2:
            log.warning("Dropping short frame list: %r", raw)
            return
        identity, frames = raw[0], raw[1:]

        try:
            msg = Message.from_frames(*frames)
        except Exception as exc:
            log.warning("Could not parse incoming Message: %s (frames=%r)", exc, frames)
            return

        if msg.header_elements.message_type != MessageTypes.JSON:
            log.warning("Ignoring non-JSON message_type=%s", msg.header_elements.message_type)
            return

        if not msg.payload:
            log.warning("Ignoring message with empty payload")
            return

        request_bytes = msg.payload[0]
        # Published for the duration of dispatch so RPC methods can consult the
        # caller without threading a parameter through every signature.
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
            return

        try:
            response_obj = json.loads(response_str)
        except json.JSONDecodeError as exc:
            log.error("RPCServer returned non-JSON response: %s", exc)
            return

        reply = Message(
            receiver=msg.sender or SERVER_NAME,
            sender=SERVER_NAME,
            data=response_obj,
            conversation_id=msg.conversation_id,
            message_type=MessageTypes.JSON,
        )
        try:
            socket.send_multipart([identity] + reply.to_frames())
        except zmq.ZMQError as exc:
            log.warning("send_multipart failed: %s", exc)
