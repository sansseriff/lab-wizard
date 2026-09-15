"""Pytest configuration ensuring package imports work.

Adds the repository root (one level above the package directory) to sys.path so
`import snspd_measure` succeeds when tests are executed from inside the
`snspd_measure` directory structure.
"""

from __future__ import annotations

import sys
import pathlib
import types as _types_mod

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PACKAGE_ROOT = REPO_ROOT / "snspd_measure"
for p in [PACKAGE_ROOT, PACKAGE_ROOT / "lib"]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


# Provide a global fake serial module so instruments don't attempt real hardware access
class _FakeSerial:
    def __init__(self, *_, **__):  # type: ignore[no-untyped-def]
        self.is_open = True
        self._buffer = b"0.0"

    def close(self):
        self.is_open = False

    def flush(self):
        pass

    def write(self, data: bytes):  # noqa: D401
        # simplistic: store last command; could tailor responses by command later
        self._last = data
        return len(data)

    def readline(self) -> bytes:  # noqa: D401
        return b"0.0\n"

    def read_all(self) -> bytes:
        return b""

    def read(self, size: int):  # noqa: D401
        return b""


_fake_serial_module = _types_mod.ModuleType("serial")
_fake_serial_module.Serial = _FakeSerial  # type: ignore[attr-defined]
sys.modules["serial"] = _fake_serial_module

# ---- Mock requests for dbay Comm ----
from typing import Any, Dict


class _FakeResponse:
    def __init__(self, data: Dict[str, Any]):
        self._data: Dict[str, Any] = data
        self.status_code = 200

    def json(self) -> Dict[str, Any]:
        return self._data


def _fake_get(url: str, *_, **__):  # type: ignore[no-untyped-def]
    if url.endswith("full-state"):
        from typing import Dict, Any, List  # type: ignore

        channels: list[dict[str, Any]] = [
            {
                "index": i,
                "bias_voltage": 0.0,
                "activated": False,
                "heading_text": f"ch{i}",
                "measuring": False,
            }
            for i in range(4)
        ]
        module0: dict[str, Any] = {
            "core": {"slot": 0, "type": "other", "name": "OtherMod"},
            "vsource": {"channels": []},
        }
        module1: dict[str, Any] = {
            "core": {"slot": 1, "type": "dac4D", "name": "Dac4D"},
            "vsource": {"channels": channels},
        }
        full_state: dict[str, Any] = {"data": [module0, module1]}
        return _FakeResponse(full_state)
    return _FakeResponse({"status": "ok", "url": url})


def _fake_put(url: str, json=None, *_, **__):  # type: ignore[no-untyped-def]
    return _FakeResponse({"status": "ok", "url": url, "data": json})


import requests  # type: ignore

requests.get = _fake_get  # type: ignore[assignment]
requests.put = _fake_put  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _isolated_lease_dir(tmp_path_factory, monkeypatch):
    """Keep transport leases out of ``~/.lab_wizard/leases``.

    Generated setup files claim their transports for the length of a run, and
    tests run them — including as subprocesses, which inherit this environment.
    A real lease directory would let a test collide with a measurement actually
    running on this machine, or leave a claim behind for one.
    """
    monkeypatch.setenv("LAB_WIZARD_LEASE_DIR", str(tmp_path_factory.mktemp("leases")))
