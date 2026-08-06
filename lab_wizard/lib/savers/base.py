from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from lab_wizard.lib.savers.saver import GenericSaver


class SaverParams(BaseModel):
    """Base class for all saver Params.

    Auto-discovered from ``lab_wizard/lib/savers/`` by resource_catalog
    (kind="saver"). Concrete subclasses must define a ``type: Literal[...]``
    discriminator field and override ``resource_class``.

    Unlike instrument Params, savers carry no hardware addressing — their
    config dict-key is simply a user-given name (e.g. "main_db", "csv_backup").
    """

    enabled: bool = True
    attribute_name: str | None = ""

    @classmethod
    def resource_class(cls) -> type["GenericSaver"]:
        raise NotImplementedError(f"{cls.__name__} must override resource_class()")

    def create_inst(self) -> "GenericSaver":
        return type(self).resource_class().from_params(self)

    def model_dump_for_yaml(self) -> dict[str, Any]:
        return self.model_dump()
