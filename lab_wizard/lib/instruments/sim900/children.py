"""Nominal Params family for modules installed in a SIM900 mainframe."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class Sim900ModuleParams(ChildParams[Any]):
    """Base for every slot-addressed SIM900 module Params class."""

