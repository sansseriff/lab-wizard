"""The workspace's data settings: how a run's folder of files is laid out.

A project only says whether its runs are saved as files too (``outputs.files``
in the project YAML). Where the folders go and how they are named is one choice
for the whole lab, kept in ``<config_dir>/data.yaml``::

    files:
      root: ""                                   # empty: <data_dir>/files
      path: "{date}/{procedure}_{device}_{time}"
      plot_png: true

A folder tree can only be ordered one way, so ``path`` is a template of the
same keys the Data page filters by::

    path: "{date}/{procedure}_{device}_{time}"          # the default
    path: "{device.wafer}/{device}/{date}_{procedure}"  # a lab that thinks by device

A workspace with no ``data.yaml`` uses these defaults.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = ["DEFAULT_TEMPLATE", "DataSettings", "FileSettings", "load_data_settings", "save_data_settings"]

SETTINGS_NAME = "data.yaml"
DEFAULT_TEMPLATE = "{date}/{procedure}_{device}_{time}"


class FileSettings(BaseModel):
    """Where each run's folder of CSV and YAML files goes, and what it holds."""

    model_config = ConfigDict(extra="forbid", json_schema_serialization_defaults_required=True)

    root: str = Field(
        default="",
        description="Folder to save runs under; empty for the workspace's data/files. A relative path is relative to the workspace.",
    )
    path: str = Field(
        default=DEFAULT_TEMPLATE,
        description="Where each run goes under the root, from filter keys: {date} {time} {procedure} {device} {device.<property>} {operator} {run.<metadata>} {param.<path>} {run_id}",
    )
    plot_png: bool = Field(default=True, description="Also save the run's default plot as plot.png")


class DataSettings(BaseModel):
    """Everything in ``data.yaml``."""

    model_config = ConfigDict(extra="forbid")

    files: FileSettings = Field(default_factory=FileSettings)


def load_data_settings(config_dir: str | Path | None) -> DataSettings:
    """The workspace's data settings, or the defaults if it has none."""
    if config_dir is None:
        return DataSettings()
    path = Path(config_dir) / SETTINGS_NAME
    if not path.is_file():
        return DataSettings()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return DataSettings.model_validate(data)


def save_data_settings(config_dir: str | Path, settings: DataSettings) -> Path:
    path = Path(config_dir) / SETTINGS_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(settings.model_dump(mode="json"), sort_keys=False), encoding="utf-8")
    return path
