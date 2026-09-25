"""Pytest configuration ensuring package imports work.

Adds the repository root (one level above the package directory) to sys.path so
`import snspd_measure` succeeds when tests are executed from inside the
`snspd_measure` directory structure.
"""

from __future__ import annotations

import sys
import pathlib
import tempfile
from pathlib import Path

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
PACKAGE_ROOT = REPO_ROOT / "snspd_measure"
for p in [PACKAGE_ROOT, PACKAGE_ROOT / "lib"]:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


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


# ---- The simulated bench ----
#
# Tests that measure something run lab_wizard's real drivers against lab_sim's
# simulated bench: a SIM900 rack behind a Prologix controller (on a
# pseudo-terminal), a 53220A counter and an AQ2212 attenuator (on TCP ports),
# all wired to one simulated SNSPD. Each test gets its own bench, so a port one
# test leaves open never blocks the next and every detector starts cold.


class Rig:
    """A running bench, and the lab_wizard config that points at it."""

    PARTS = ("source", "meter", "counter", "attenuator")

    def __init__(self, bench: Any) -> None:
        from lab_wizard.lib.utilities.config_io import instrument_hash

        self.bench = bench
        self.model = bench.model
        c = bench.config
        self.port = bench.prologix_port
        counter_host, counter_port = bench.counter_address
        yoko_host, yoko_port = bench.aq2212_address
        self.gpib = instrument_hash("prologix_gpib", self.port)
        self.mainframe = instrument_hash("sim900", str(c.prologix.gpib_address))
        self.source = instrument_hash("sim928", str(c.prologix.source_slot))
        self.meter = instrument_hash("sim970", str(c.prologix.voltmeter_slot))
        self.counter = instrument_hash("keysight53220A", f"{counter_host}:{counter_port}")
        self.yoko = instrument_hash("yokogawa_aq2212", f"{yoko_host}:{yoko_port}")
        self.attenuator = instrument_hash("yoko_attenuator", str(c.attenuator.slot))

    # -- config -------------------------------------------------------------

    def gpib_params(self, parts: tuple[str, ...] = ("source", "meter")) -> Any:
        from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
        from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
        from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970ChannelParams, Sim970Params
        from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params

        c = self.bench.config.prologix
        modules: dict[str, Any] = {}
        if "source" in parts:
            modules[self.source] = Sim928Params(slot=str(c.source_slot))
        if "meter" in parts:
            # Nothing physical settles; the real 0.1 s would dominate every sweep.
            channels = {i: Sim970ChannelParams(settling_time=0.0) for i in range(4)}
            modules[self.meter] = Sim970Params(slot=str(c.voltmeter_slot), channels=channels)
        return PrologixGPIBParams(
            port=self.port,
            # The bench answers in well under a millisecond; a short timeout
            # keeps the silence a test provokes (a scan of 30 addresses) cheap.
            timeout=0.05,
            children={self.mainframe: Sim900Params(gpib_address=str(c.gpib_address), children=modules)},
        )

    def counter_params(self) -> Any:
        from lab_wizard.lib.instruments.keysight53220A import Keysight53220AParams

        host, port = self.bench.counter_address
        return Keysight53220AParams(ip_address=host, ip_port=port)

    def yoko_params(self) -> Any:
        from lab_wizard.lib.instruments.yokogawaAQ2212.modules.attenuator import YokoAttenuatorParams
        from lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212 import YokogawaAQ2212Params

        host, port = self.bench.aq2212_address
        return YokogawaAQ2212Params(
            ip_address=host, ip_port=port,
            children={self.attenuator: YokoAttenuatorParams(slot=str(self.bench.config.attenuator.slot))},
        )

    def instruments(self, parts: tuple[str, ...] = PARTS) -> dict[str, Any]:
        """The top-level instrument entries for ``parts`` of the bench."""
        out: dict[str, Any] = {}
        if {"source", "meter"} & set(parts):
            out[self.gpib] = self.gpib_params(parts)
        if "counter" in parts:
            out[self.counter] = self.counter_params()
        if "attenuator" in parts:
            out[self.yoko] = self.yoko_params()
        return out

    def write(self, config_dir: Path, parts: tuple[str, ...] = PARTS) -> dict[str, Any]:
        """Save ``parts`` of the bench as a workspace's instrument config."""
        from lab_wizard.lib.utilities.config_io import (
            assign_missing_leaf_attribute_names,
            save_instruments_to_config,
        )

        instruments = self.instruments(parts)
        assign_missing_leaf_attribute_names(instruments)
        save_instruments_to_config(instruments, config_dir)
        return instruments

    # -- selections ---------------------------------------------------------

    def path(self, part: str) -> list[Any]:
        """The chain from ``part`` up to its root, as the generator wants it."""
        from lab_wizard.wizard.backend.project_generation import SelectedNodeRef as Ref

        rack = [Ref(type="sim900", key=self.mainframe), Ref(type="prologix_gpib", key=self.gpib)]
        return {
            "source": [Ref(type="sim928", key=self.source), *rack],
            "meter": [Ref(type="sim970", key=self.meter), *rack],
            "counter": [Ref(type="keysight53220A", key=self.counter)],
            "attenuator": [Ref(type="yoko_attenuator", key=self.attenuator), Ref(type="yokogawa_aq2212", key=self.yoko)],
        }[part]

    def select(self, role: str, part: str, channel_index: int | None = None) -> Any:
        """Fill procedure ``role`` with ``part`` of the bench."""
        from lab_wizard.wizard.backend.project_generation import SelectedResource

        path = self.path(part)
        if channel_index is None and part in ("meter", "counter"):
            channel_index = 0
        return SelectedResource(
            variable_name=role, type=path[0].type, key=path[0].key, channel_index=channel_index, path=path,
        )


@pytest.fixture
def make_rig():
    """Start a bench, optionally with its own detector constants: ``make_rig(noise_volts=1e-5)``."""
    from lab_sim import Bench, BenchConfig

    benches: list[Any] = []

    def make(**detector: Any) -> Rig:
        # A short path: the link is a serial port name the drivers print and hash.
        link = Path(tempfile.mkdtemp(prefix="sim")) / "prologix"
        config = BenchConfig.model_validate({
            "detector": detector,
            "prologix": {"link": str(link)},
            "counter": {"port": 0},
            "attenuator": {"port": 0},
        })
        bench = Bench(config).start()
        benches.append(bench)
        return Rig(bench)

    yield make
    for bench in benches:
        bench.stop()


@pytest.fixture
def rig(make_rig) -> Rig:
    """A standard bench, cold, for this test alone."""
    return make_rig()
