"""Where a project keeps its measurement module, and how it is loaded.

A generated project runs its setup file as a script, so Python puts the
project's folder first on ``sys.path``. A module sitting there under a bare
name — ``queue.py``, ``logging.py``, ``test.py`` — would be what *every*
``import`` of that name in the process finds, the standard library's own
included. So the measurement module is named after its setup file, with a
suffix no standard module has::

    projects/iv_curve_20260929_120000/
        iv_curve_20260929_120000.yaml
        iv_curve_setup.py
        iv_curve_measurement.py

and it is loaded by its path (:func:`load_module`) rather than imported. The
setup file imports it by name only for type checkers, so an editor still
follows its classes to their code.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

__all__ = ["MODULE_SUFFIX", "load_module", "module_path"]

MODULE_SUFFIX = "_measurement"


def module_path(project_dir: str | Path, name: str) -> Path:
    """The measurement module ``name`` of the project at ``project_dir``."""
    return Path(project_dir) / f"{name}{MODULE_SUFFIX}.py"


def load_module(path: str | Path) -> ModuleType:
    """Load the Python file at ``path`` as a module, fresh each time.

    Loaded by path under a name of its own, never imported by its bare file
    name, so it cannot shadow another module. Loading fresh is what lets the
    wizard see an edit without restarting. The file's folder goes on the *end*
    of ``sys.path``, so a helper beside it can be imported but never hides an
    installed module.
    """
    path = Path(path).resolve()
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.append(folder)
    module_name = f"lab_wizard_measurements.{path.parent.name}.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError(f"{path} is not a Python module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses look their module up here
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module
