"""Nominal Params family for Ando AQ8201A modules."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class AndoAQ8201AModuleParams(ChildParams[Any]):
    """Base for every slot-addressed Ando module Params class."""

