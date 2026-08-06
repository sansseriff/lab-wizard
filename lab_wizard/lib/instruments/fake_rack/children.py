"""Nominal Params families used by the simulated rack."""

from typing import Any

from lab_wizard.lib.instruments.general.parent_child import ChildParams


class FakeGpibChildParams(ChildParams[Any]):
    """Base for simulated devices addressed on a fake GPIB bus."""


class Fake900ModuleParams(ChildParams[Any]):
    """Base for simulated modules installed in a FAKE900 slot."""

