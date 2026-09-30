"""Whether the lab database sits on a network share, where SQLite is not safe.

SQLite is a library in each process that opens the file, not a server. The
processes coordinate through file locks and, in the WAL mode the lab database
uses, through a shared-memory file every one of them maps. Shared memory cannot
span machines, and network filesystems' locks are often not what they claim,
so a database on an SMB or NFS share that two machines write to will
eventually be corrupted. One machine writing to it alone usually works, but
nothing stops a second.

So nothing here refuses a share; it only says so, once, where a person will
see it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

__all__ = ["NETWORK_WARNING", "network_filesystem", "warn_if_networked"]

logger = logging.getLogger(__name__)

_NETWORK_TYPES = {
    "nfs", "nfs4", "cifs", "smbfs", "smb3", "smb2", "afpfs", "webdav", "davfs",
    "sshfs", "fuse.sshfs", "9p", "ceph", "glusterfs", "fuse.glusterfs", "lustre", "gpfs",
}

NETWORK_WARNING = (
    "The lab database is on a network share ({kind}). SQLite is only safe when "
    "every process that writes it runs on one machine; two machines recording "
    "into it will eventually corrupt it. Keep data_dir (lab-wizard.toml) on a "
    "local disk, and share runs through the wizard or exported files."
)


def _mounts() -> list[tuple[str, str]]:
    """``(mount point, filesystem type)`` for every mounted filesystem, as far as can be told."""
    if sys.platform.startswith("linux"):
        try:
            lines = Path("/proc/mounts").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            parts = line.split()
            if len(parts) >= 3:
                out.append((parts[1].replace("\\040", " "), parts[2]))
        return out
    # macOS and the BSDs: "//user@host/share on /Volumes/share (smbfs, nodev, ...)"
    try:
        text = subprocess.run(["mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in text.splitlines():
        if " on " not in line or "(" not in line:
            continue
        where = line.split(" on ", 1)[1]
        point, _, options = where.rpartition(" (")
        out.append((point, options.split(",")[0].strip(" )")))
    return out


def _windows_remote(path: Path) -> bool:
    if str(path).startswith("\\\\"):
        return True
    import ctypes

    drive = path.drive + "\\"
    DRIVE_REMOTE = 4
    return ctypes.windll.kernel32.GetDriveTypeW(drive) == DRIVE_REMOTE  # type: ignore[attr-defined]


def network_filesystem(path: str | Path) -> str | None:
    """The network filesystem type ``path`` is on (``"smbfs"``, ``"nfs"``), or ``None`` if local."""
    path = Path(path).expanduser()
    # The file may not exist yet; its nearest existing folder is on the same filesystem.
    while not path.exists() and path.parent != path:
        path = path.parent
    path = path.resolve()
    if sys.platform == "win32":
        try:
            return "network drive" if _windows_remote(path) else None
        except Exception:  # noqa: BLE001 - a check that cannot run says nothing
            return None
    best = ("", "")
    for point, kind in _mounts():
        if (str(path) == point or str(path).startswith(point.rstrip(os.sep) + os.sep)) and len(point) > len(best[0]):
            best = (point, kind)
    kind = best[1].lower()
    return kind if kind in _NETWORK_TYPES or kind.startswith("nfs") else None


def warn_if_networked(path: str | Path) -> str | None:
    """Log the network-share warning for ``path`` if it applies; returns it."""
    kind = network_filesystem(path)
    if kind is None:
        return None
    message = NETWORK_WARNING.format(kind=kind)
    logger.warning("%s (%s)", message, path)
    return message
