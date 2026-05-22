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
from typing import Any, Optional

import zmq

from pyleco.core.message import Message
from pyleco.core.serialization import MessageTypes
from pyleco.json_utils.errors import JSONRPCError
from pyleco.json_utils.json_objects import JsonRpcError
from pyleco.json_utils.rpc_server import RPCServer

from lab_wizard.lib.server.permissions import PermissionGate
from lab_wizard.lib.server.registry import InstrumentRegistry


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
        bind: str,
        registry: InstrumentRegistry,
        gate: Optional[PermissionGate] = None,
    ) -> None:
        self._bind = bind
        self._registry = registry
        self._gate = gate

        self._rpc = RPCServer(title="lab_wizard_server")
        self._rpc.method()(self.call)
        self._rpc.method()(self.list_paths)
        self._rpc.method()(self.list_attributes)
        self._rpc.method()(self.describe_path)
        self._rpc.method()(self.describe_attribute)
        self._rpc.method()(self.list_descriptions)

        self._ctx = zmq.Context.instance()
        self._socket: Optional[zmq.Socket] = None
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
        self._socket = self._ctx.socket(zmq.ROUTER)
        self._socket.bind(self._bind)
        self._running = True
        log.info("WireServer listening on %s", self._bind)

        poller = zmq.Poller()
        poller.register(self._socket, zmq.POLLIN)

        try:
            while self._running:
                events = dict(poller.poll(timeout=200))
                if self._socket in events:
                    self._handle_one()
        finally:
            if self._socket is not None:
                self._socket.close(linger=0)
                self._socket = None

    def stop(self) -> None:
        self._running = False

    # ------------------------- Internals -------------------------

    def _handle_one(self) -> None:
        assert self._socket is not None
        try:
            raw = self._socket.recv_multipart()
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
        response_str = self._rpc.process_request(request_bytes)
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
            self._socket.send_multipart([identity] + reply.to_frames())
        except zmq.ZMQError as exc:
            log.warning("send_multipart failed: %s", exc)
