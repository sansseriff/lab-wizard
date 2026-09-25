"""A simulated SNSPD test bench, served on this machine for lab_wizard to measure.

Run ``lab-sim`` and point lab_wizard's ordinary instrument drivers at the
addresses it prints. See :mod:`lab_sim.bench`.
"""

from lab_sim.bench import Bench, BenchConfig
from lab_sim.snspd import SnspdModel, SnspdParams

__all__ = ["Bench", "BenchConfig", "SnspdModel", "SnspdParams"]
