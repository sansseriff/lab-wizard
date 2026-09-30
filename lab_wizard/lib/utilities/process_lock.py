"""Whether a process is still alive, told by a lock it holds rather than by its pid.

A pid outlives its process: after the process exits, or after a reboot, the
same number can belong to something else, and ``os.kill(pid, 0)`` then says
"alive" about the wrong process. A lock does not have that problem. The
operating system releases it the moment its holder exits, however it exits —
a crash, ``kill -9``, a power cut followed by a reboot.

So a process that others need to watch holds an exclusive lock on a file for as
long as it lives, and a watcher asks whether that file is still locked:

    held = hold(path)        # in the watched process; kept until it exits
    is_held(path)            # anywhere else: True while that process lives

The lock is advisory and local to one machine. On a network share, locks are
only as reliable as the share's locking.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

__all__ = ["HeldLock", "LockBusy", "hold", "is_held"]


class LockBusy(RuntimeError):
    """Another process holds the lock."""


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class HeldLock:
    """An exclusive lock on ``path``, held until :meth:`release` or process exit."""

    def __init__(self, path: Path, stream: IO[bytes]) -> None:
        self.path = path
        self._stream: IO[bytes] | None = stream

    def fileno(self) -> int:
        if self._stream is None:
            raise ValueError("lock already released")
        return self._stream.fileno()

    def release(self) -> None:
        """Let go of the lock and remove its file."""
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        # Removed while still locked, so no watcher sees an unlocked file that
        # belonged to a process still finishing.
        self.path.unlink(missing_ok=True)
        try:
            _unlock(stream.fileno())
        finally:
            stream.close()

    def hand_off(self) -> None:
        """Close this process's copy *without* unlocking, after passing it to a child.

        On POSIX a lock belongs to the open file, which a child started with
        ``pass_fds=[lock.fileno()]`` shares. Unlocking here would unlock it for
        the child too; closing only drops this process's reference, so the lock
        then lives exactly as long as the child does.
        """
        if self._stream is not None:
            stream, self._stream = self._stream, None
            stream.close()

    def __enter__(self) -> "HeldLock":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


def hold(path: str | Path) -> HeldLock:
    """Take the lock on ``path``, creating the file; ``LockBusy`` if someone has it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    if not _try_lock(stream.fileno()):
        stream.close()
        raise LockBusy(f"{path} is held by another process")
    return HeldLock(path, stream)


def is_held(path: str | Path) -> bool:
    """Whether some live process holds the lock on ``path``. A missing file is not held."""
    try:
        stream = Path(path).open("rb")
    except FileNotFoundError:
        return False
    try:
        if not _try_lock(stream.fileno()):
            return True
        _unlock(stream.fileno())
        return False
    finally:
        stream.close()
