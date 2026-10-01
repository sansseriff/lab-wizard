"""A generated project, as its setup file runs it.

A project's YAML says *which* instrument fills each role (``roles:``) and its
params; its setup file says *what class* each role is, in a ``Resources``
dataclass whose fields are typed by the driver behind them — ``Sim928``, or
``RemoteCounter`` for an instrument reached through a server. :class:`Project`
joins the two, one visible step at a time, so the setup's ``__main__`` reads
as what happens::

    measurement = measurement_module(Path(__file__).parent)   # rebuilt if procedure: changed
    project = Project.load(Path(__file__).parent)
    status = RunLifecycle(
        claims=project.local_claims(),                 # the racks this run opens
        claims_after_resolve=[project.routed_claims],  # the instruments on servers
    ).run(
        resolve=lambda: project.resources(Resources),  # each role, checked against its type
        execute=lambda resources: project.run(measurement.build_procedure(resources), resources),
    )

A composed procedure lives in the ``procedure:`` block of the project's YAML.
Its measurement module, ``<name>_measurement.py``, is built from that block
and records the block's hash; :func:`measurement_module` builds it again, before
loading it, whenever the block has changed. So the YAML is the only copy anyone
edits, the Python an editor follows is always the Python that runs, and each
run records the very block its code was built from.

Every role is checked against the class the setup declares for it before any
instrument is built: a local one by the class its config says it is, so a
mismatch never opens a port. The YAML and the setup are written together; if
one is edited and the other is not, the run refuses to start and says which
role disagrees, rather than handing a measurement the wrong instrument.
"""

from __future__ import annotations

import dataclasses
import typing
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar, get_args, get_origin

from lab_procedure import ProcedureError, Status, Step

from lab_wizard.lib.client.claims import RoutedClaims
from lab_wizard.lib.client.local_claims import LocalTransportClaim
from lab_wizard.lib.client.project_resources import _workspace_instruments, local_claims_for, resource_source_for
from lab_wizard.lib.client.proxies.registry import PROXY_BY_BEHAVIOR
from lab_wizard.lib.procedures.codegen import built_from, measurement_module_source, procedure_hash
from lab_wizard.lib.procedures.definition import ProcedureDefinition
from lab_wizard.lib.project_module import load_module, module_path
from lab_wizard.lib.task_adapters.run import run_procedure
from lab_wizard.lib.utilities.model_tree import ProjectConfig, RoleBinding, _find_attribute_path, load_project_config
from lab_wizard.lib.utilities.python_formatting import format_python_code

__all__ = ["Project", "RoleMismatch", "build_measurement_module", "measurement_module"]

R = TypeVar("R")


class RoleMismatch(TypeError):
    """A role's instrument is not the class the setup file declares for it."""


class Project:
    """One project folder: its YAML, and the instruments it binds to its roles."""

    def __init__(self, directory: Path, config: ProjectConfig, *, remote: str | None = None) -> None:
        self.directory = directory
        self.config = config
        # ``--remote``: every instrument through this one server instead.
        self.remote = remote
        self._source: Any = None

    @classmethod
    def load(cls, directory: str | Path, *, remote: str | None = None) -> "Project":
        """The project in ``directory``, from its ``<folder name>.yaml``."""
        directory = Path(directory).resolve()
        return cls(directory, load_project_config(directory / f"{directory.name}.yaml"), remote=remote)

    @property
    def name(self) -> str:
        return self.directory.name

    # ------------------------------------------------------------ claims

    def local_claims(self) -> list[LocalTransportClaim]:
        """Claims on the racks this process opens itself: the local roles' roots."""
        return local_claims_for(self.config, self.directory, owner=self.name, remote=self.remote)

    def routed_claims(self, instruments: list[Any]) -> RoutedClaims:
        """Claims on the instruments reached through a server, once they are resolved."""
        return RoutedClaims(instruments, holder=self.name)

    # ------------------------------------------------------------ roles

    def resources(self, cls: type[R]) -> R:
        """Build ``cls``: each role's instrument from ``roles:``, and ``params`` from the YAML.

        Every instrument is checked against its field's type first.
        """
        hints = typing.get_type_hints(cls)
        self._check_local_roles(cls, hints)
        values: dict[str, Any] = {}
        for field in dataclasses.fields(cls):  # type: ignore[arg-type]
            hint = hints[field.name]
            if field.name == "params":
                values["params"] = hint.model_validate(self.config.measurement.params)
                continue
            binding = self.config.roles.get(field.name)
            if binding is None:
                raise RoleMismatch(
                    f"The setup file has a role {field.name!r}, but {self.name}.yaml binds no "
                    "instrument to it under roles:. Regenerate the project, or add it."
                )
            if get_origin(hint) is list:
                (element,) = get_args(hint)
                bindings = binding if isinstance(binding, list) else [binding]
                values[field.name] = [self._instrument(field.name, b, element) for b in bindings]
            elif isinstance(binding, list):
                raise RoleMismatch(f"roles.{field.name} lists several instruments, but the setup file takes one")
            else:
                values[field.name] = self._instrument(field.name, binding, hint)
        return cls(**values)

    def _check_local_roles(self, cls: type, hints: dict[str, Any]) -> None:
        """Refuse a local role whose configured instrument is not its declared class, before building any."""
        if self.remote:
            return
        local = {
            name: binding
            for name, binding in self.config.roles.items()
            if not isinstance(binding, list) and binding.server is None
        }
        if not local:
            return
        instruments = _workspace_instruments(self.directory)
        for field in dataclasses.fields(cls):  # type: ignore[arg-type]
            binding = local.get(field.name)
            expected = hints.get(field.name)
            if binding is None or not isinstance(expected, type):
                continue
            found = _find_attribute_path(instruments, binding.instrument)
            if found is None:
                raise RoleMismatch(
                    f"roles.{field.name} is {binding.instrument}, but no instrument in this "
                    "workspace's config/instruments has that attribute_name. It may have been "
                    "renamed or removed since the project was generated."
                )
            path, channel = found
            configured = type(path[-1][1]).resource_class()
            if channel is not None:
                configured = configured.channel_class
            if not issubclass(configured, expected):
                raise self._mismatch(field.name, binding, configured, expected)

    def _mismatch(self, role: str, binding: RoleBinding, found: type, expected: type) -> RoleMismatch:
        where = f" on {binding.server}" if binding.server else ""
        return RoleMismatch(
            f"roles.{role} is {binding.instrument}{where}, a {found.__name__}, but the setup "
            f"file expects a {expected.__name__}. The YAML and the setup disagree: regenerate the "
            "project, or change the type in the setup file."
        )

    def _instrument(self, role: str, binding: RoleBinding, expected: type) -> Any:
        if self._source is None:
            self._source = resource_source_for(self.config, self.directory, remote=self.remote)
        found = self._source.from_attribute(binding.instrument)
        if isinstance(found, expected) or (self.remote and _does_what(found, expected)):
            return found
        raise self._mismatch(role, binding, type(found), expected)

    # ------------------------------------------------------------ running

    @property
    def definition(self) -> dict[str, Any] | None:
        """The procedure every run records: the ``procedure:`` block its code was built from."""
        return self.config.procedure

    def run(self, root: Step, resources: Any, *, definition: dict[str, Any] | None = None) -> Status:
        """Run ``root`` once, recorded with this project's run details and outputs.

        The run records ``definition``, by default this project's procedure.
        """
        return run_procedure(
            root,
            resources,
            procedure=self.config.measurement_type,
            definition=definition if definition is not None else self.definition,
            project_dir=self.directory,
        )


# ------------------------------------------------------------ the measurement module


def build_measurement_module(project_dir: str | Path, *, force: bool = False) -> bool:
    """Build ``<name>_measurement.py`` from the YAML's ``procedure:`` block, if it changed.

    Returns whether it was built. A custom measurement's module is its own
    code, so it is never built. ``ProcedureError`` says what is wrong with a
    block that cannot be built.
    """
    directory = Path(project_dir).resolve()
    config = load_project_config(directory / f"{directory.name}.yaml")
    if config.procedure is None:
        return False
    path = module_path(directory, config.measurement_type)
    if not force and path.is_file() and built_from(path.read_text(encoding="utf-8")) == procedure_hash(config.procedure):
        return False
    try:
        definition = ProcedureDefinition.model_validate(config.procedure)
    except ValueError as e:
        raise ProcedureError([f"the procedure: block of {directory.name}.yaml: {e}"]) from e
    source = format_python_code(measurement_module_source(definition, config.procedure))
    path.write_text(source, encoding="utf-8")
    return True


def measurement_module(project_dir: str | Path) -> ModuleType:
    """The project's measurement module, built again first if its ``procedure:`` changed."""
    directory = Path(project_dir).resolve()
    rebuilt = build_measurement_module(directory)
    config = load_project_config(directory / f"{directory.name}.yaml")
    path = module_path(directory, config.measurement_type)
    if rebuilt:
        print(f"lab_wizard: built {path.name} again from the changed procedure: block of {directory.name}.yaml", flush=True)
    return load_module(path)


def _does_what(found: Any, expected: type) -> bool:
    """With ``--remote``, a local driver's role is filled by a proxy: it must do what the driver does."""
    behaviors = [b for b in PROXY_BY_BEHAVIOR if issubclass(expected, b)]
    return bool(behaviors) and all(isinstance(found, b) for b in behaviors)
