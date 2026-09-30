"""Savers: files a run writes beside its lab database record.

A saver is a :class:`~lab_wizard.lib.task_adapters.sinks.RunSink`, like any
other output of a run. A project asks for the file saver with
``outputs.files``; any other sink is handed to
:func:`~lab_wizard.lib.task_adapters.run.run_procedure` directly.
"""

from .file_saver import FileSaver

__all__ = ["FileSaver"]
