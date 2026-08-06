from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from lab_wizard.lib.plotters.plotter import GenericPlotter


class PlotterParams(BaseModel):
    """Base class for all plotter Params.

    Auto-discovered from ``lab_wizard/lib/plotters/`` by resource_catalog
    (kind="plotter"). Concrete subclasses must define a ``type: Literal[...]``
    discriminator field and override ``resource_class``.

    Unlike instrument Params, plotters carry no hardware addressing — their
    config dict-key is a user-given name (e.g. "main_window", "iv_grid").
    """

    enabled: bool = True
    attribute_name: str | None = ""

    @classmethod
    def resource_class(cls) -> type["GenericPlotter"]:
        raise NotImplementedError(f"{cls.__name__} must override resource_class()")

    def create_inst(self) -> "GenericPlotter":
        return type(self).resource_class().from_params(self)

    def model_dump_for_yaml(self) -> dict[str, Any]:
        return self.model_dump()
