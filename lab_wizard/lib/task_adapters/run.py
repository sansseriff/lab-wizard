"""Run a project's procedure: record it, save it as files, plot it.

Generated projects call :func:`run_procedure` and nothing else, so what a run
records and who listens to it can change here without regenerating a project.
See ``plans/semantic_data_plan.md`` §4.

A run started from a project is always recorded in the lab database: the
workspace's ``<data_dir>/lab.db``, or, for a project outside any workspace, the
project's own ``data/lab.db``. A step tree run without a project (a test, a
notebook) records nothing unless given a recorder.

What else a run produces is read from the project's ``outputs:`` block each
time it starts: a folder of files (``files``, laid out by the workspace's
``data.yaml``) and a live plot (``live_plot``).

A run the wizard launches (``LAUNCH_FILE_ENV`` set) is drawn on the wizard's
Run page, so it opens no plot of its own; it writes its run id to that file
as soon as it has one, which is how the wizard finds the run to follow.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lab_procedure import ProcedureRunner, RunStarted, Status, Step

from lab_wizard.lib.data import DATABASE_NAME, DatabaseRecorder
from lab_wizard.lib.data.settings import load_data_settings
from lab_wizard.lib.plotters import GenericPlotter, MplPlotter, WebPlotter
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.savers import FileSaver, GenericSaver, SaverContext
from lab_wizard.lib.task_adapters.plotters import PlotterSink
from lab_wizard.lib.task_adapters.provenance import baseline_snapshot
from lab_wizard.lib.utilities.model_tree import (
    OutputsConfig,
    ProjectConfig,
    load_project_config,
)
from lab_wizard.lib.workspace import find_workspace

logger = logging.getLogger(__name__)

__all__ = ["LAUNCH_FILE_ENV", "RunSinks", "attach_sinks", "database_path", "project_outputs", "run_procedure", "run_started"]

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


def project_outputs(project_dir: Path) -> tuple[list[GenericSaver], list[GenericPlotter]]:
    """The savers and plotters the project at ``project_dir`` asks for in ``outputs:``."""
    project = _project(project_dir)
    outputs = project.outputs if project is not None else OutputsConfig()

    savers: list[GenericSaver] = []
    if outputs.files:
        workspace = find_workspace(project_dir, use_environment=False)
        files = load_data_settings(workspace.config_dir if workspace is not None else None).files
        root: Path | None = None
        if files.root:
            root = Path(files.root).expanduser()
            if not root.is_absolute():
                root = (workspace.root if workspace is not None else project_dir) / root
        savers.append(FileSaver(root=root, path=files.path, plot_png=files.plot_png))

    plotters: list[GenericPlotter] = []
    if os.environ.get(LAUNCH_FILE_ENV):
        pass  # the wizard's Run page draws it (plans/runner_plan.md R6)
    elif outputs.live_plot == "window":
        plotters.append(MplPlotter(plot=outputs.plot))
    elif outputs.live_plot == "web":
        plotters.append(WebPlotter(plot=outputs.plot))
    return savers, plotters


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


@dataclass
class RunSinks:
    """What ``attach_sinks`` subscribed that ``run_procedure`` looks after afterwards."""

    recorder: DatabaseRecorder | None
    plotters: list[GenericPlotter]


def _report_run_id(recorder: DatabaseRecorder, path: Path) -> Any:
    """Write the run's id to ``path`` once the recorder has it, for the wizard."""

    def handle(message: RunStarted) -> None:
        if recorder.run_id is not None:
            path.write_text(json.dumps({"run_id": recorder.run_id, "database": str(recorder.path)}), encoding="utf-8")

    return handle


def attach_sinks(
    runner: ProcedureRunner,
    *,
    project_dir: Path | None = None,
    savers: list[GenericSaver] | None = None,
    plotters: list[GenericPlotter] | None = None,
) -> RunSinks:
    """Subscribe everything that consumes a run: the database, then savers and plotters.

    ``savers`` and ``plotters`` of ``None`` mean the ones the project's
    ``outputs:`` asks for (none without a project). The recorder is subscribed
    first, so it has the run's database id before any saver or plotter sees the
    run start.
    """
    recorder = None
    if project_dir is not None:
        recorder = DatabaseRecorder(database_path(project_dir))
        recorder.attach(runner.context.data_bus, runner.context.status_bus)
        wanted_savers, wanted_plotters = project_outputs(project_dir)
        savers = wanted_savers if savers is None else savers
        plotters = wanted_plotters if plotters is None else plotters
        launch_file = os.environ.get(LAUNCH_FILE_ENV)
        if launch_file:
            runner.context.data_bus.subscribe((RunStarted,), _report_run_id(recorder, Path(launch_file)))
    context = SaverContext(project_dir=project_dir, recorder=recorder)
    for saver in savers or []:
        saver.attach(runner.context.data_bus, runner.context.status_bus, context)
    PlotterSink(plotters or [], recorder).attach(runner.context.data_bus)
    return RunSinks(recorder=recorder, plotters=list(plotters or []))


def run_procedure(
    root: Step,
    resources: Any,
    *,
    procedure: str,
    definition: dict[str, Any] | None = None,
    project_dir: str | Path | None = None,
    savers: list[GenericSaver] | None = None,
    plotters: list[GenericPlotter] | None = None,
) -> Status:
    """Run ``root`` against ``resources``, recording it if it belongs to a project.

    ``savers`` and ``plotters`` replace the ones the project's ``outputs:`` asks for.
    """
    project_path = Path(project_dir).resolve() if project_dir is not None else None
    runner = ProcedureRunner(instruments=resources)
    sinks = attach_sinks(runner, project_dir=project_path, savers=savers, plotters=plotters)
    try:
        started = run_started(procedure, resources, definition=definition, project_dir=project_path)
        return runner.run(root, started)
    finally:
        if sinks.recorder is not None:
            sinks.recorder.close()
        for plotter in sinks.plotters:
            try:
                plotter.finish()
            except Exception:  # noqa: BLE001 - the run is over and recorded either way
                logger.exception("%s failed while finishing", type(plotter).__name__)
