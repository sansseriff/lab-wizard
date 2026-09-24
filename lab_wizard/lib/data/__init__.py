"""The lab database: where every run is recorded, and how it is read back.

See ``plans/semantic_data_plan.md``.
"""

from lab_wizard.lib.data.recorder import DatabaseRecorder
from lab_wizard.lib.data.schema import DATABASE_NAME, SCHEMA_VERSION, DatabaseVersionError, open_database

__all__ = ["DATABASE_NAME", "SCHEMA_VERSION", "DatabaseRecorder", "DatabaseVersionError", "open_database"]
