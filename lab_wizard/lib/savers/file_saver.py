"""Save each run as a folder of plain files, for people who work with files.

The run is still recorded in the lab database either way; this is an extra copy
laid out for a file browser and a spreadsheet (see
:mod:`lab_wizard.lib.data.run_folder` for what a folder holds).

A folder tree can only be ordered one way, so where each run goes is a
template of the same keys the Data page filters by::

    path: "{date}/{procedure}_{device}_{time}"        # the default
    path: "{device.wafer}/{device}/{date}_{procedure}"  # a lab that thinks by device
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from lab_procedure import Point, RunEnded, RunStarted, StepBegan, StepEnded
from lab_procedure.messages import NodeId
from pydantic import Field

from lab_wizard.lib.data.encoding import jsonable
from lab_wizard.lib.data.run_folder import RunFolder, run_facets_for_folder
from lab_wizard.lib.savers.base import SaverParams
from lab_wizard.lib.savers.saver import GenericSaver

__all__ = ["FileSaver", "FileSaverParams"]

DEFAULT_TEMPLATE = "{date}/{procedure}_{device}_{time}"


class FileSaverParams(SaverParams):
    """Params for saving each run as a folder of CSV and YAML files."""

    type: Literal["file_saver"] = "file_saver"
    root: str = Field(
        default="",
        description="Folder to save runs under; empty for the workspace's data/files. A relative path is relative to the project.",
    )
    path: str = Field(
        default=DEFAULT_TEMPLATE,
        description="Where each run goes under the root, from filter keys: {date} {time} {procedure} {device} {device.<property>} {operator} {run.<metadata>} {param.<path>} {run_id}",
    )
    plot_png: bool = Field(default=True, description="Also save the run's default plot as plot.png")

    @classmethod
    def resource_class(cls) -> type["FileSaver"]:
        return FileSaver


class FileSaver(GenericSaver):
    """Writes each run it sees to a new folder under ``root``."""

    def __init__(self, root: str = "", path: str = DEFAULT_TEMPLATE, plot_png: bool = True) -> None:
        self.root = root
        self.template = path
        self.plot_png = plot_png
        self.folder: RunFolder | None = None
        self._run: dict[str, Any] = {}
        self._open_steps: dict[NodeId, list[int]] = {}
        self._next_step = 0

    @classmethod
    def from_params(cls, params: SaverParams) -> "FileSaver":
        if not isinstance(params, FileSaverParams):
            raise TypeError(f"FileSaver.from_params expected FileSaverParams, got {type(params).__name__}")
        return cls(root=params.root, path=params.path, plot_png=params.plot_png)

    def _root(self) -> Path:
        context = self.context
        if self.root:
            root = Path(self.root).expanduser()
            if root.is_absolute():
                return root
            if context.project_dir is None:
                raise ValueError(f"file_saver root {self.root!r} is relative, and this run has no project")
            return context.project_dir / root
        if context.database is not None:
            return context.database.parent / "files"
        if context.project_dir is not None:
            return context.project_dir / "data" / "files"
        raise ValueError("file_saver has no root: set one, or run from a project")

    def handle(self, message: Any) -> None:
        if isinstance(message, RunStarted):
            self._run_started(message)
        elif self.folder is None:
            return
        elif isinstance(message, Point):
            self.folder.add_point(message.seq, message.t.isoformat(), list(message.steps), message.values)
        elif isinstance(message, StepBegan):
            key = self._next_step
            self._next_step += 1
            self._open_steps.setdefault(message.node_id, []).append(key)
            self.folder.step_began(key, "/".join(message.node_id), message.kind, message.t.isoformat())
        elif isinstance(message, StepEnded):
            keys = self._open_steps.get(message.node_id)
            if keys:
                self.folder.step_ended(keys.pop(), message.t.isoformat(), message.status, message.error)
        elif isinstance(message, RunEnded):
            self._run.update(status=message.status, ended_at=message.t.isoformat())
            self.folder.finish(self._run, plot_png=self.plot_png)

    def _run_started(self, message: RunStarted) -> None:
        self._open_steps = {}
        self._next_step = 0
        self._run = {
            "run_id": self.context.run_id,
            "procedure": message.procedure,
            "status": "running",
            "started_at": message.t.isoformat(),
            "ended_at": None,
            "device": message.device,
            "operator": message.operator,
            "notes": message.notes,
            "project": message.project,
            "metadata": jsonable(message.metadata),
            "params": jsonable(message.params),
            "instruments": jsonable(message.instruments),
            "columns": jsonable(message.columns),
        }
        facets = run_facets_for_folder(self._run, self.context.device(message.device))
        self.folder = RunFolder.create(self._root(), self.template, facets, self._run, message.definition)
