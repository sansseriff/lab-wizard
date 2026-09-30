"""Run a project's procedure: record it, save it as files, plot it.

Generated projects call :func:`run_procedure` and nothing else, so what a run
records and who listens to it can change here without regenerating a project.
See ``plans/semantic_data_plan.md`` §4.

A run started from a project is always recorded in the lab database: the
workspace's ``<data_dir>/lab.db``, or, for a project outside any workspace, the
project's own ``data/lab.db``. A step tree run without a project (a test, a
notebook) records nothing unless given a database.

What else a run produces is read from the project's ``outputs:`` block each
time it starts: a folder of files (``files``, laid out by the workspace's
``data.yaml``) and a live plot (``live_plot``). Each is a
:class:`~lab_wizard.lib.task_adapters.sinks.RunSink`, fed after the database
has recorded each message (:class:`~lab_wizard.lib.task_adapters.sinks.RunOutputs`).

A run the wizard launches (``LAUNCH_FILE_ENV`` set) is drawn on the wizard's
Run page, so it opens no plot of its own; it writes its run id to that file
as soon as it has one, which is how the wizard finds the run to follow.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from lab_procedure import ProcedureRunner, RunStarted, Status, Step

from lab_wizard.lib.data import DATABASE_NAME, DatabaseRecorder
from lab_wizard.lib.data.settings import load_data_settings
from lab_wizard.lib.plotters import MplPlotter, WebPlotter
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.savers import FileSaver
from lab_wizard.lib.task_adapters.provenance import baseline_snapshot
from lab_wizard.lib.task_adapters.sinks import RunInfo, RunOutputs, RunSink
from lab_wizard.lib.utilities.model_tree import (
    OutputsConfig,
    ProjectConfig,
    load_project_config,
)
from lab_wizard.lib.workspace import find_workspace

logger = logging.getLogger(__name__)

__all__ = [
    "LAUNCH_FILE_ENV",
    "LaunchReport",
    "database_path",
    "project_outputs",
    "run_outputs",
    "run_procedure",
    "run_started",
]

# Set by the wizard when it launches a run: a file to write the run's id to.
LAUNCH_FILE_ENV = "LAB_WIZARD_LAUNCH_FILE"


def database_path(project_dir: str | Path) -> Path:
    """Where runs of the project at ``project_dir`` are recorded.

    The workspace the project sits in is found from the project's own location,
    never from the process's working directory or the environment, so a run
    lands in the same database however it was started.
    """
    project_dir = Path(project_dir).resolve()
    workspace = find_workspace(project_dir, use_environment=False)
    root = workspace.data_dir if workspace is not None else project_dir / "data"
    return root / DATABASE_NAME


def _project(project_dir: Path | None) -> ProjectConfig | None:
    if project_dir is None:
        return None
    path = project_dir / f"{project_dir.name}.yaml"
    return load_project_config(path) if path.is_file() else None


def project_outputs(project_dir: Path) -> list[RunSink]:
    """What the project at ``project_dir`` asks a run to produce in ``outputs:``."""
    project = _project(project_dir)
    outputs = project.outputs if project is not None else OutputsConfig()

    sinks: list[RunSink] = []
    if outputs.files:
        workspace = find_workspace(project_dir, use_environment=False)
        files = load_data_settings(workspace.config_dir if workspace is not None else None).files
        root: Path | None = None
        if files.root:
            root = Path(files.root).expanduser()
            if not root.is_absolute():
                root = (workspace.root if workspace is not None else project_dir) / root
        sinks.append(FileSaver(root=root, path=files.path, plot_png=files.plot_png))

    if os.environ.get(LAUNCH_FILE_ENV):
        pass  # the wizard's Run page draws it (plans/runner_plan.md R6)
    elif outputs.live_plot == "window":
        sinks.append(MplPlotter(plot=outputs.plot))
    elif outputs.live_plot == "web":
        sinks.append(WebPlotter(plot=outputs.plot))
    return sinks


def run_started(
    procedure: str,
    resources: Any,
    *,
    definition: dict[str, Any] | None = None,
    project_dir: Path | None = None,
) -> RunStarted:
    """Everything known about a run of ``procedure`` before it starts."""
    project = _project(project_dir)
    run = project.run if project is not None else None
    params = getattr(resources, "params", None)
    return RunStarted(
        procedure=procedure,
        device=(run.device or None) if run else None,
        operator=(run.operator or None) if run else None,
        notes=(run.notes or None) if run else None,
        project=project_dir.name if project_dir is not None else None,
        metadata=dict(run.metadata) if run else {},
        definition=definition,
        params=params.model_dump(mode="json") if hasattr(params, "model_dump") else {},
        instruments=baseline_snapshot(resources),
        # A procedure's columns are known before it runs. A custom measurement's
        # definition only carries its plots, and its columns are learned as they come.
        columns=ProcedureDefinition.model_validate(definition).columns() if definition and "body" in definition else {},
    )


class LaunchReport(RunSink):
    """Writes the run's id to ``path`` once it has one, for the wizard that launched it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def handle(self, message: Any, run: RunInfo) -> None:
        if isinstance(message, RunStarted) and run.run_id is not None:
            self.path.write_text(json.dumps({"run_id": run.run_id, "database": str(run.database)}), encoding="utf-8")


def run_outputs(
    *,
    project_dir: Path | None = None,
    database: str | Path | None = None,
    sinks: list[RunSink] | None = None,
) -> RunOutputs:
    """Everything that consumes a run: its lab database record, then its sinks.

    A run in a project is recorded in the project's workspace database, and
    ``sinks`` of ``None`` means the ones its ``outputs:`` asks for. A run
    outside a project is recorded only if given a ``database``, and produces
    only the ``sinks`` it is given.
    """
    recorder = None
    wanted: list[RunSink] = []
    if project_dir is not None:
        recorder = DatabaseRecorder(database_path(project_dir))
        wanted = project_outputs(project_dir)
        launch_file = os.environ.get(LAUNCH_FILE_ENV)
        if launch_file:
            wanted.insert(0, LaunchReport(Path(launch_file)))
    elif database is not None:
        recorder = DatabaseRecorder(database)
    return RunOutputs(recorder, wanted if sinks is None else sinks, project_dir=project_dir)


def run_procedure(
    root: Step,
    resources: Any,
    *,
    procedure: str,
    definition: dict[str, Any] | None = None,
    project_dir: str | Path | None = None,
    sinks: list[RunSink] | None = None,
) -> Status:
    """Run ``root`` against ``resources``, recording it if it belongs to a project.

    ``sinks`` replace the ones the project's ``outputs:`` asks for.
    """
    project_path = Path(project_dir).resolve() if project_dir is not None else None
    runner = ProcedureRunner(instruments=resources)
    outputs = run_outputs(project_dir=project_path, sinks=sinks)
    outputs.attach(runner.context.data_bus, runner.context.status_bus)
    try:
        started = run_started(procedure, resources, definition=definition, project_dir=project_path)
        return runner.run(root, started)
    finally:
        outputs.close()
