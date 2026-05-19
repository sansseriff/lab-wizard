from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from lab_wizard.lib.plotters.plotter import GenericPlotter


class PlotterParams(BaseModel):
    """Base class for all plotter Params.

    Auto-discovered from ``lab_wizard/lib/plotters/`` by params_discovery
    (kind="plotter"). Concrete subclasses must define a ``type: Literal[...]``
    discriminator field and override ``inst`` to point at their runtime class.

    Unlike instrument Params, plotters carry no hardware addressing — their
    config dict-key is a user-given name (e.g. "main_window", "iv_grid").
    """

    enabled: bool = True
    attribute_name: str | None = ""

    @property
    def inst(self) -> type["GenericPlotter"]:
        raise NotImplementedError(
            f"{type(self).__name__} must override the 'inst' property"
        )

    def create_inst(self) -> "GenericPlotter":
        return self.inst.from_params(self)

    def model_dump_for_yaml(self) -> dict[str, Any]:
        return self.model_dump()
