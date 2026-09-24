"""Run a project's procedure: record it, feed its savers and plotters.

Generated projects call :func:`run_procedure` and nothing else, so what a run
records and who listens to it can change here without regenerating a project.
See ``plans/semantic_data_plan.md`` §4.

A run started from a project is always recorded in the lab database: the
workspace's ``<data_dir>/lab.db``, or, for a project outside any workspace, the
project's own ``data/lab.db``. A step tree run without a project (a test, a
notebook) records nothing unless given a recorder.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from lab_procedure import Point, ProcedureRunner, RunEnded, RunStarted, Status, Step

from lab_wizard.lib.data import DATABASE_NAME, DatabaseRecorder
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.task_adapters.plotters import PlotterSink
from lab_wizard.lib.task_adapters.provenance import baseline_snapshot
from lab_wizard.lib.task_adapters.savers import SaverSink
from lab_wizard.lib.utilities.model_tree import ProjectConfig, load_project_config
from lab_wizard.lib.workspace import find_workspace

__all__ = ["attach_sinks", "database_path", "run_procedure", "run_started"]


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
        columns=ProcedureDefinition.model_validate(definition).columns() if definition else {},
    )


def attach_sinks(runner: ProcedureRunner, resources: Any, *, project_dir: Path | None = None) -> DatabaseRecorder | None:
    """Subscribe everything that consumes a run: the database, then savers and plotters.

    The recorder is subscribed first, so it has the run's database id before
    any other sink sees the run start.
    """
    recorder = None
    if project_dir is not None:
        recorder = DatabaseRecorder(database_path(project_dir))
        recorder.attach(runner.context.data_bus, runner.context.status_bus)
    messages = (RunStarted, Point, RunEnded)
    runner.context.data_bus.subscribe(messages, SaverSink(getattr(resources, "savers", [])).handle)
    runner.context.data_bus.subscribe(messages, PlotterSink(getattr(resources, "plotters", [])).handle)
    return recorder


def run_procedure(
    root: Step,
    resources: Any,
    *,
    procedure: str,
    definition: dict[str, Any] | None = None,
    project_dir: str | Path | None = None,
) -> Status:
    """Run ``root`` against ``resources``, recording it if it belongs to a project."""
    project_path = Path(project_dir).resolve() if project_dir is not None else None
    runner = ProcedureRunner(instruments=resources)
    recorder = attach_sinks(runner, resources, project_dir=project_path)
    try:
        started = run_started(procedure, resources, definition=definition, project_dir=project_path)
        return runner.run(root, started)
    finally:
        if recorder is not None:
            recorder.close()
