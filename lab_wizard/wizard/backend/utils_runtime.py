"""Runtime utilities for environment detection, ANSI coloring, and networking.

These helpers are kept lightweight and avoid non-stdlib deps. If psutil is
installed, we leverage it to enumerate network interfaces; otherwise we fall
back to portable stdlib methods.
"""
from __future__ import annotations

import os
import sys
import socket
import logging
from typing import List, Tuple

logger = logging.getLogger("lab_wizard.wizard.backend.utils_runtime")


def is_ssh_session() -> bool:
    """Heuristic: detect if we're running under SSH by env vars."""
    return any(os.environ.get(var) for var in ("SSH_CONNECTION", "SSH_TTY", "SSH_CLIENT"))


def has_gui_context() -> bool:
    """Best-effort check for an available GUI context.

    - Linux: require DISPLAY or WAYLAND_DISPLAY
    - macOS: assume GUI exists unless over SSH
    - Windows: assume GUI available
    """
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")) and not is_ssh_session()
    if sys.platform == "darwin":
        # On macOS, a local Terminal session typically has GUI access; SSH sessions don't.
        return not is_ssh_session()
    if sys.platform.startswith("win"):
        return not is_ssh_session()
    # Unknown platform: be conservative
    return False


def green(text: str) -> str:
    """Return text wrapped in green ANSI codes when stdout is a TTY."""
    try:
        if sys.stdout.isatty():
            return f"\033[92m{text}\033[0m"
    except Exception:
        pass
    return text


def _is_loopback(ip: str) -> bool:
    return ip.startswith("127.") or ip == "0.0.0.0"


def get_ipv4_addresses_detailed() -> List[Tuple[str, str]]:
    """Enumerate non-loopback IPv4 addresses, including per-interface aliases.

    Returns a list of (interface_name, ip) tuples. Uses psutil when available
    to capture all addresses (including VPN/utun, alias IPs). Falls back to
    stdlib-based heuristics providing minimal coverage.
    """
    detailed: List[Tuple[str, str]] = []

    # Prefer psutil for full interface and alias enumeration
    try:
        import psutil  # type: ignore

        if_addrs = psutil.net_if_addrs()
        try:
            if_stats = psutil.net_if_stats()
        except Exception:
            if_stats = {}

        for ifname, addrs in if_addrs.items():
            # Optionally filter to interfaces that are UP; include if unknown
            is_up = True
            st = if_stats.get(ifname)
            if st is not None:
                is_up = bool(getattr(st, "isup", True))
            if not is_up:
                continue

            for a in addrs:
                if getattr(a, "family", None) == socket.AF_INET:
                    ip = a.address
                    if ip and not _is_loopback(ip):
                        detailed.append((ifname, ip))
    except Exception as e:
        # Fallbacks: produce at least one candidate with a generic name
        logger.debug("psutil interface scan unavailable; using fallback discovery: %s", e)

    # If psutil path produced nothing, try hostname and UDP probes
    if not detailed:
        try:
            host = socket.gethostname()
            _, _, host_ips = socket.gethostbyname_ex(host)
            for ip in host_ips:
                if ip and not _is_loopback(ip):
                    detailed.append(("hostdns", ip))
        except Exception as e:
            logger.debug("Hostname IPv4 discovery failed: %s", e)

        for probe in ("8.8.8.8", "1.1.1.1"):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.connect((probe, 80))
                ip = s.getsockname()[0]
                s.close()
                if ip and not _is_loopback(ip):
                    detailed.append(("egress", ip))
            except Exception as e:
                logger.debug("Egress IPv4 probe %s failed: %s", probe, e)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: List[Tuple[str, str]] = []
    for name, ip in detailed:
        if ip not in seen:
            deduped.append((name, ip))
            seen.add(ip)
    return deduped


def get_ipv4_addresses() -> List[str]:
    """Convenience wrapper returning only IP strings (deduplicated)."""
    return [ip for _, ip in get_ipv4_addresses_detailed()]
