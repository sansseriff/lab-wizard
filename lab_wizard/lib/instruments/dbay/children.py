"""Nominal Params family for modules installed in a DBay."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class DBayModuleParams(ChildParams[Any]):
    """Base for every DBay module Params class."""

