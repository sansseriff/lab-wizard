"""ZMQ DEALER session for talking to a lab_wizard server.

Phase 2 behavior:
- One persistent DEALER socket, opened on construction.
- ``call`` is synchronous: one request in flight at a time, guarded by a lock.
- Server-side errors come back as JSON-RPC ``{"error": {...}}`` and are raised
  as ``RemoteCallError`` carrying ``code``, ``message``, ``data``.
- ``call_inst(path, method, args, kwargs)`` is the convenience wrapper used by
  proxies; it forwards to the server's ``call`` RPC.

Phase 4 will add reconnect/backoff and concurrent in-flight requests.
"""

from __future__ import annotations

import threading
import uuid
from types import TracebackType
from typing import Any, Optional, Type

import zmq

from pyleco.core.message import Message
from pyleco.core.serialization import MessageTypes


class RemoteCallError(RuntimeError):
    """A JSON-RPC error response from the server."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"[{code}] {message}" + (f" — {data!r}" if data is not None else ""))
        self.code = code
        self.message = message
        self.data = data


# JSON-RPC server-error code the server uses for permission denials.
PERMISSION_DENIED_CODE = -32001


class PermissionDeniedError(RemoteCallError):
    """Raised when the server's permission gate blocks a call.

    Subclasses RemoteCallError, so ``except RemoteCallError`` still catches it.
    ``rule_id`` and ``blocking_state`` come from the server's error ``data``.
    """

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        super().__init__(code, message, data)
        info = data if isinstance(data, dict) else {}
        self.rule_id: Optional[str] = info.get("rule_id")
        self.blocking_state: dict[str, Any] = info.get("blocking_state", {})


class Session:
    """Synchronous DEALER client for one server endpoint."""

    def __init__(
        self,
        url: str,
        *,
        timeout_ms: int = 10_000,
        client_name: str = "lab_wizard_client",
        auto_reconnect: bool = True,
    ) -> None:
        self._url = url
        self._timeout_ms = timeout_ms
        self._client_name = client_name.encode()
        self._auto_reconnect = auto_reconnect

        self._ctx = zmq.Context.instance()
        self._socket = self._open_socket()

        self._lock = threading.Lock()
        self._closed = False

    def _open_socket(self) -> "zmq.Socket":
        socket = self._ctx.socket(zmq.DEALER)
        socket.setsockopt(zmq.LINGER, 0)
        socket.setsockopt(zmq.RCVTIMEO, self._timeout_ms)
        socket.connect(self._url)
        return socket

    def _reconnect(self) -> None:
        """Replace the socket. Caller holds the lock."""
        try:
            self._socket.close(linger=0)
        except Exception:  # noqa: BLE001 - already unusable
            pass
        self._socket = self._open_socket()

    def _exchange(self, msg: Message) -> list[bytes]:
        """One send/receive. Raises TimeoutError if nothing comes back."""
        self._socket.send_multipart(msg.to_frames())
        try:
            return self._socket.recv_multipart()
        except zmq.Again as exc:
            raise TimeoutError(
                f"No response from {self._url} within {self._timeout_ms}ms"
            ) from exc

    @property
    def url(self) -> str:
        return self._url

    # ------------------------- low-level RPC -------------------------

    def call(self, method: str, params: Optional[dict[str, Any] | list[Any]] = None) -> Any:
        """Send a JSON-RPC request; return ``result`` or raise ``RemoteCallError``."""
        if self._closed:
            raise RuntimeError("Session is closed")

        request: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
        }
        if params is not None:
            request["params"] = params

        msg = Message(
            receiver=b"lab_wizard_server",
            sender=self._client_name,
            data=request,
            message_type=MessageTypes.JSON,
        )

        with self._lock:
            try:
                frames = self._exchange(msg)
            except TimeoutError:
                # A DEALER queues sends against a dead peer and then waits out
                # the full timeout, so a server restart leaves the socket
                # permanently useless with no error to distinguish it from a
                # slow instrument. Rebuilding the socket clears the queued
                # request and gives the retry a clean connection.
                if not self._auto_reconnect:
                    raise
                self._reconnect()
                frames = self._exchange(msg)

        reply = Message.from_frames(*frames)
        data = reply.data
        if not isinstance(data, dict):
            raise RuntimeError(f"Malformed response (expected dict): {data!r}")
        if "error" in data and data["error"] is not None:
            err = data["error"]
            code = err.get("code", 0)
            err_cls = (
                PermissionDeniedError
                if code == PERMISSION_DENIED_CODE
                else RemoteCallError
            )
            raise err_cls(
                code=code,
                message=err.get("message", ""),
                data=err.get("data"),
            )
        return data.get("result")

    # ------------------------- convenience -------------------------

    def call_inst(
        self,
        path: str,
        method: str,
        args: Optional[list[Any]] = None,
        kwargs: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Invoke ``method`` on the instrument at ``path``."""
        return self.call(
            "call",
            {
                "path": path,
                "method": method,
                "args": args or [],
                "kwargs": kwargs or {},
            },
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._socket.close(linger=0)

    def __enter__(self) -> "Session":
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> None:
        self.close()
