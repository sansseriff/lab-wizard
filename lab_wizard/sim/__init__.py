"""A simulated SNSPD test bench, served on this machine for lab_wizard to measure.

Run ``wizard sim`` and point lab_wizard's ordinary instrument drivers at the
addresses it prints. See :mod:`lab_wizard.sim.bench`, and
:mod:`lab_wizard.sim.background` for running it in the background.
"""

from lab_wizard.sim.bench import Bench, BenchConfig
from lab_wizard.sim.snspd import SnspdModel, SnspdParams

__all__ = ["Bench", "BenchConfig", "SnspdModel", "SnspdParams"]
