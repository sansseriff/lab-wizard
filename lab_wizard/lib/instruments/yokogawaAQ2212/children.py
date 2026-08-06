"""Nominal Params family for Yokogawa AQ2212 modules."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class YokogawaAQ2212ModuleParams(ChildParams[Any]):
    """Base for every slot-addressed Yokogawa module Params class."""

