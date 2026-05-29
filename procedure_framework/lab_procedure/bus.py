from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any


class MessageBus:
    """Synchronous, thread-safe fan-out for typed messages."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._subscribers: list[tuple[tuple[type[Any], ...], Callable[[Any], None]]] = []

    def subscribe(
        self,
        msg_types: type[Any] | tuple[type[Any], ...],
        callback: Callable[[Any], None],
    ) -> Callable[[], None]:
        types = msg_types if isinstance(msg_types, tuple) else (msg_types,)
        entry = (types, callback)
        with self._lock:
            self._subscribers.append(entry)

        def unsubscribe() -> None:
            with self._lock:
                if entry in self._subscribers:
                    self._subscribers.remove(entry)

        return unsubscribe

    def emit(self, message: Any) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for types, callback in subscribers:
            if isinstance(message, types):
                callback(message)
