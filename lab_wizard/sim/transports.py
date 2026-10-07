"""How the lab's drivers reach the simulated instruments: the same way as real ones.

* A USB instrument (the Prologix GPIB controller) is a serial port to the
  computer. The simulator opens a **pseudo-terminal**, a serial port with a
  program instead of a cable on the other end, and links it at a stable path.
  The driver opens that path with pyserial exactly as it opens
  ``/dev/tty.usbserial-…``, exclusive lock included.
* A network instrument (the counter, the AQ2212 frame) is a TCP port. The
  simulator listens on one and answers a line of SCPI with a line.

**One thread serves them all, in the order the drivers meant.** A write to a
serial port or a socket returns once the bytes are in the kernel, so a driver
that sets the bias over the serial port and then reads the counter over TCP
has sent the two in that order, but nothing makes them *arrive* in it. What a
driver does do is wait on a query, and on nothing else, so a query is the one
thing that can overtake a write. The :class:`Hub` therefore takes in whatever
is waiting on every connection, takes it all in once more if any of it asks a
question (a write sent just before the question is in the kernel by then), and
handles the writes before the questions. The one detector every instrument
shares is never read ahead of a change made to it.
"""

from __future__ import annotations

import errno
import logging
import os
import select
import selectors
import socket
import threading
import tty
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger("lab_wizard.sim.transports")

Feed = Callable[[bytes], bytes]


def line_feed(handle_line: Callable[[str], Optional[str]]) -> Feed:
    """Frame a byte stream into lines for ``handle_line``; replies end in ``\\n``."""
    pending = b""

    def feed(data: bytes) -> bytes:
        nonlocal pending
        pending += data
        *lines, pending = pending.split(b"\n")
        out = bytearray()
        for raw in lines:
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            reply = handle_line(line)
            if reply is not None:
                out += reply.encode() + b"\n"
        return bytes(out)

    return feed


class _Stream:
    """One open byte stream the hub reads from and answers on."""

    def __init__(self, fd: int, feed: Feed, name: str, sock: socket.socket | None = None):
        self.fd = fd
        self.feed = feed
        self.name = name
        self.sock = sock

    def read(self) -> bytes | None:
        """What is waiting, ``b""`` if nothing, ``None`` once the other end has gone."""
        try:
            data = os.read(self.fd, 65536)
        except BlockingIOError:
            return b""
        except OSError as exc:
            # A pseudo-terminal nobody has open reads EIO; the port is still
            # there for the next driver to open.
            if exc.errno == errno.EIO and self.sock is None:
                return b""
            return None
        if not data:
            return b"" if self.sock is None else None
        return data

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                view = view[os.write(self.fd, view):]
            except BlockingIOError:
                select.select([], [self.fd], [], 1.0)


class PtySerial:
    """A serial port at ``link``: a pseudo-terminal whose far end is ``feed``."""

    def __init__(self, feed: Feed, link: Path):
        self.feed = feed
        self.link = link
        self.master, self._slave = os.openpty()
        # Raw from the start: no echo, no line editing, no CR/LF translation.
        # pyserial sets the same when it opens the port; this covers the bytes
        # before it does. The simulator keeps its own handle on the terminal
        # side open, so the port survives a driver closing and reopening it.
        tty.setraw(self._slave)
        os.set_blocking(self.master, False)
        self.device = os.ttyname(self._slave)
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(self.device)

    def close(self) -> None:
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass
        if self.link.is_symlink() and os.readlink(self.link) == self.device:
            self.link.unlink()


class TcpPort:
    """A listening TCP port; each connection gets its own ``make_feed()``."""

    def __init__(self, make_feed: Callable[[], Feed], host: str, port: int):
        self.make_feed = make_feed
        self.sock = socket.create_server((host, port), reuse_port=False)
        self.sock.setblocking(False)
        self.host, self.port = self.sock.getsockname()[:2]

    def close(self) -> None:
        self.sock.close()


class Hub:
    """Serves every port of the bench from one thread."""

    def __init__(self) -> None:
        self._selector = selectors.DefaultSelector()
        self._streams: dict[int, _Stream] = {}
        self._ports: list[PtySerial | TcpPort] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="lab_wizard sim hub", daemon=True)

    def add_serial(self, port: PtySerial) -> None:
        self._ports.append(port)
        self._open(_Stream(port.master, port.feed, str(port.link)))

    def add_tcp(self, port: TcpPort) -> None:
        self._ports.append(port)
        self._selector.register(port.sock, selectors.EVENT_READ, port)

    def _open(self, stream: _Stream) -> None:
        self._streams[stream.fd] = stream
        self._selector.register(stream.fd, selectors.EVENT_READ, stream)

    def _close(self, stream: _Stream) -> None:
        self._streams.pop(stream.fd, None)
        self._selector.unregister(stream.fd)
        if stream.sock is not None:
            stream.sock.close()

    # -- serving -------------------------------------------------------------

    def start(self) -> None:
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            ready = self._selector.select(timeout=0.05)
            for key, _ in ready:
                if isinstance(key.data, TcpPort):
                    self._accept(key.data)
            if ready:
                self._sweep()

    def _accept(self, port: TcpPort) -> None:
        try:
            sock, _ = port.sock.accept()
        except BlockingIOError:
            return
        sock.setblocking(False)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._open(_Stream(sock.fileno(), port.make_feed(), f"tcp {port.port}", sock))

    def _take_in(self, waiting: dict[_Stream, bytes]) -> None:
        for stream in list(self._streams.values()):
            data = stream.read()
            if data is None:
                self._close(stream)
                waiting.pop(stream, None)
            elif data:
                waiting[stream] = waiting.get(stream, b"") + data

    def _sweep(self) -> None:
        waiting: dict[_Stream, bytes] = {}
        self._take_in(waiting)
        if any(b"?" in data for data in waiting.values()):
            self._take_in(waiting)
        # Writes first: everything before the first line that asks a question.
        questions: list[tuple[_Stream, bytes]] = []
        for stream, data in waiting.items():
            mark = data.find(b"?")
            if mark == -1:
                self._answer(stream, data)
                continue
            start = data.rfind(b"\n", 0, mark) + 1
            self._answer(stream, data[:start])
            questions.append((stream, data[start:]))
        for stream, data in questions:
            self._answer(stream, data)

    def _answer(self, stream: _Stream, data: bytes) -> None:
        if not data or stream.fd not in self._streams:
            return
        reply = stream.feed(data)
        if reply:
            try:
                stream.write(reply)
            except OSError:  # the other end hung up mid-reply
                self._close(stream)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=1.0)
        for stream in list(self._streams.values()):
            if stream.sock is not None:
                stream.sock.close()
        for port in self._ports:
            port.close()
        self._selector.close()
