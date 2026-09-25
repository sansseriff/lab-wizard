"""The lab database: where every run is recorded, and how it is read back.

Reading, for a notebook::

    from lab_wizard.lib.data import find, load_plot
    runs = find(procedure="mcr_curve")
    df = runs.points()

See ``plans/semantic_data_plan.md``.
"""

from lab_wizard.lib.data.expressions import ExpressionError, compile_expression, derive
from lab_wizard.lib.data.plot import PlotSpec, evaluate_plot, load_plot, notebook_source, to_series
from lab_wizard.lib.data.read import Lab, Runs, facets, find, lab_database
from lab_wizard.lib.data.recorder import DatabaseRecorder
from lab_wizard.lib.data.schema import DATABASE_NAME, SCHEMA_VERSION, DatabaseVersionError, open_database

__all__ = [
    "DATABASE_NAME",
    "SCHEMA_VERSION",
    "DatabaseRecorder",
    "DatabaseVersionError",
    "ExpressionError",
    "Lab",
    "PlotSpec",
    "Runs",
    "compile_expression",
    "derive",
    "evaluate_plot",
    "facets",
    "find",
    "lab_database",
    "load_plot",
    "notebook_source",
    "open_database",
    "to_series",
]
