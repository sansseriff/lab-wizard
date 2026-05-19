from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from lab_wizard.lib.savers.saver import GenericSaver


class SaverParams(BaseModel):
    """Base class for all saver Params.

    Auto-discovered from ``lab_wizard/lib/savers/`` by params_discovery
    (kind="saver"). Concrete subclasses must define a ``type: Literal[...]``
    discriminator field and override ``inst`` to point at their runtime class.

    Unlike instrument Params, savers carry no hardware addressing — their
    config dict-key is simply a user-given name (e.g. "main_db", "csv_backup").
    """

    enabled: bool = True
    attribute_name: str | None = ""

    @property
    def inst(self) -> type["GenericSaver"]:
        raise NotImplementedError(
            f"{type(self).__name__} must override the 'inst' property"
        )

    def create_inst(self) -> "GenericSaver":
        return self.inst.from_params(self)

    def model_dump_for_yaml(self) -> dict[str, Any]:
        return self.model_dump()
