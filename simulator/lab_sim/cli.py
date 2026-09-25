"""``lab-sim``: serve the simulated bench until interrupted."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path

from lab_sim.bench import Bench, BenchConfig


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="lab-sim", description="Serve a simulated SNSPD test bench on this machine.")
    parser.add_argument("config", nargs="?", type=Path, help="bench YAML (detector constants, ports); defaults if omitted")
    parser.add_argument("-v", "--verbose", action="store_true", help="log every unrecognised command")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING, format="%(name)s: %(message)s")

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    with Bench(BenchConfig.load(args.config)) as bench:
        print(bench.describe(), flush=True)
        print("Ctrl-C to stop.", flush=True)
        try:
            stop.wait()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
