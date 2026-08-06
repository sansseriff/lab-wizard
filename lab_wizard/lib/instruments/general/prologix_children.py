"""Nominal Params family accepted by a Prologix GPIB controller."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class PrologixChildParams(ChildParams[Any]):
    """Base for every instrument addressed through a Prologix controller."""

