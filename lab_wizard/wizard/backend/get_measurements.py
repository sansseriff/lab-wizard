from typing import Dict, List, Optional, get_args, get_origin
from pathlib import Path
import importlib
import inspect
import logging

from pydantic import BaseModel

from lab_wizard.lib.plotters.plotter import GenericPlotter
from lab_wizard.lib.savers.saver import GenericSaver
from lab_wizard.lib.utilities.resource_catalog import get_instrument_metadata
from lab_wizard.wizard.backend.models import (
    FilledReq, MeasurementInfo, Env, MatchingReq,
)

logger = logging.getLogger("lab_wizard.wizard.backend.get_measurements")


def _classify_field(field_type) -> tuple[str, bool, type | None]:
    """Inspect a field annotation and classify it.

    Returns ``(resource_kind, is_list, element_type)`` where ``element_type``
    is the underlying class if ``field_type`` is a ``list[T]`` annotation, or
    ``field_type`` itself otherwise. ``resource_kind`` is one of "instrument",
    "saver", or "plotter"; "params" or "skip" indicates the field should be
    ignored.
    """
    origin = get_origin(field_type)
    is_list = origin in (list, tuple, set)
    if is_list:
        args = get_args(field_type)
        element = args[0] if args else None
    else:
        element = field_type

    if not isinstance(element, type):
        return ("skip", is_list, None)

    try:
        if issubclass(element, GenericSaver):
            return ("saver", is_list, element)
        if issubclass(element, GenericPlotter):
            return ("plotter", is_list, element)
    except TypeError:
        pass

    return ("instrument", is_list, element)


def _resources_class_for_template(env: Env, template_file: Path, verbose: bool = False):
    """The ``*Resources`` dataclass a setup template declares, or ``None``.

    The single place a measurement states what it needs — both its resource
    roles and its typed params model — so everything derived from a measurement
    reads it from here rather than keeping a parallel list.
    """
    rel_path = template_file.relative_to(env.base_dir)
    module_name = "lab_wizard.lib." + str(rel_path.with_suffix("")).replace("/", ".")
    if verbose:
        logger.debug("Importing template module: %s", module_name)

    module = importlib.import_module(module_name)

    for name, obj in inspect.getmembers(module, inspect.isclass):
        if (
            name.endswith("Resources")
            and hasattr(obj, "__annotations__")
            and obj.__module__ == module.__name__
        ):
            return obj
    return None


def params_model_for_measurement(measurement: MeasurementInfo) -> Optional[type]:
    """The typed params model a measurement declares, or ``None`` if it has none.

    Read from the template's ``params`` annotation rather than a lookup table
    keyed by measurement name. Measurements are *discovered* from the directory,
    so any table of theirs is a second list that silently falls out of date: a
    new measurement would appear in the UI, generate a project, and get an empty
    ``measurement.params`` block with nothing reporting why.

    Reading the annotation also means the default can never disagree with the
    generated setup file, because it is literally the same declaration the file
    constructs from.
    """
    template_file = _template_file_for(measurement.measurement_dir)
    if not template_file.exists():
        return None
    lib_base = _find_lib_base(template_file.parent)
    if lib_base is None:
        return None

    resources_class = _resources_class_for_template(Env(base_dir=lib_base), template_file)
    if resources_class is None:
        return None

    model = resources_class.__annotations__.get("params")
    if isinstance(model, type) and issubclass(model, BaseModel):
        return model
    if model is not None:
        logger.warning(
            "%s declares params as %r, which is not a pydantic model; the "
            "generated project will have an empty params block",
            template_file.name,
            model,
        )
    return None


def _extract_resources_from_template(
    env: Env, template_file: Path, verbose: bool = False
) -> List[FilledReq]:
    """Extract required resource types from a measurement's setup template.

    Recognizes instrument fields (any class), saver fields (subclass of
    GenericSaver), and plotter fields (subclass of GenericPlotter).  Both
    plain (``saver: GenericSaver``) and list (``savers: list[GenericSaver]``)
    annotations are supported.
    """
    required: List[FilledReq] = []

    if verbose:
        logger.debug("Running template resource extraction for %s", template_file)

    resources_class = _resources_class_for_template(env, template_file, verbose)

    if not resources_class:
        logger.warning("No Resources class found in %s", template_file)
        return required

    for field_name, field_type in resources_class.__annotations__.items():
        if field_name == "params":
            continue
        kind, is_list, element = _classify_field(field_type)
        if kind == "skip" or element is None:
            if verbose:
                logger.debug("Skipping field %s (unrecognized type %s)", field_name, field_type)
            continue
        if verbose:
            logger.debug("Resource field '%s' kind=%s is_list=%s element=%s",
                         field_name, kind, is_list, element)
        required.append(
            FilledReq(
                variable_name=field_name,
                base_type=element,
                resource_kind=kind,  # type: ignore[arg-type]
                is_list=is_list,
            )
        )

    if verbose:
        logger.debug("Required resources from template: %s", required)
    return required


# Back-compat alias for any external callers
_extract_instruments_from_template = _extract_resources_from_template


def _template_file_for(measurement_dir: Path) -> Path:
    """Return the expected setup template file path for a measurement dir."""
    return measurement_dir / f"{measurement_dir.name}_setup_template.py"


def _find_lib_base(start: Path) -> Optional[Path]:
    """Walk up from start to locate the 'lib' package root used for imports."""
    for parent in [start] + list(start.parents):
        if parent.name == "lib" and (parent / "__init__.py").exists():
            return parent
    return None


def reqs_from_measurement(measurement: MeasurementInfo, verbose: bool = False) -> List[FilledReq]:
    """Return a list of required resource roles (instruments, savers, plotters)."""
    measurement_dir = measurement.measurement_dir
    template_file = _template_file_for(measurement_dir)
    if verbose:
        logger.debug("Template file candidate: %s", template_file)

    if not template_file.exists():
        logger.warning("Template file does not exist: %s", template_file)
        return []

    lib_base = _find_lib_base(template_file.parent)
    if lib_base is None:
        logger.warning("Could not find lib base for template: %s", template_file)
        return []

    env = Env(base_dir=lib_base)
    required = _extract_resources_from_template(env, template_file)
    return required


def discover_matching_instruments(
    env: Env, base_type: type, verbose: bool = False
) -> List[MatchingReq]:
    """Match registered instruments/channels using catalog behavior metadata.

    The old implementation imported every Python file beneath ``instruments``
    for every request.  Runtime catalog auditing has already performed the
    authoritative ``issubclass`` checks and persisted their wire-safe result.
    """
    behavior_name = getattr(base_type, "__name__", str(base_type))
    found: dict[str, MatchingReq] = {}
    for info in get_instrument_metadata().values():
        for prefix, behavior_key in (
            ("resource", "behavior_abc"),
            ("channel", "channel_behavior_abc"),
        ):
            if info.get(behavior_key) != behavior_name:
                continue
            module_name = info.get(f"{prefix}_module")
            class_name = info.get(f"{prefix}_class_name")
            if not isinstance(module_name, str) or not isinstance(class_name, str):
                continue
            module_prefix = "lab_wizard.lib.instruments."
            if module_name.startswith(module_prefix):
                relative = Path(*module_name[len(module_prefix):].split(".")).with_suffix(".py")
                file_path = env.instruments_dir / relative
            else:
                file_path = env.instruments_dir
            qualname = f"{module_name}.{class_name}"
            found.setdefault(
                qualname,
                MatchingReq(
                    module=module_name,
                    class_name=class_name,
                    qualname=qualname,
                    file_path=file_path,
                    friendly_name=class_name,
                ),
            )

    matches = sorted(found.values(), key=lambda match: match.qualname)

    logger.info("Discovered %d matches for base type %s", len(matches), base_type)
    if verbose:
        logger.debug("Matches for base type %s: %s", base_type, matches)
    return matches


def get_measurements(env: Env) -> Dict[str, MeasurementInfo]:
    """Discover available measurement types."""
    measurements: Dict[str, MeasurementInfo] = {}

    for measurement_dir in env.measurements_dir.iterdir():
        if not measurement_dir.is_dir() or measurement_dir.name.startswith("__"):
            continue

        # Look for the main measurement file
        measurement_file = measurement_dir / f"{measurement_dir.name}.py"
        if not measurement_file.exists():
            continue

        # Look for the template file to analyze required instruments
        template_file = _template_file_for(measurement_dir)

        try:
            description = f"{measurement_dir.name} measurement"

            if template_file.exists():
                # Also read the file for docstring extraction
                with open(template_file, "r") as f:
                    template_content = f.read()

                # Try to extract description from docstring
                if '"""' in template_content:
                    doc_start = template_content.find('"""')
                    doc_end = template_content.find('"""', doc_start + 3)
                    if doc_end > doc_start:
                        doc_content = template_content[doc_start + 3 : doc_end]
                        first_line = doc_content.strip().split("\n")[0]
                        if first_line and "Parameters" not in first_line:
                            description = first_line

            measurements[measurement_dir.name] = MeasurementInfo(
                name=measurement_dir.name,
                description=description,
                measurement_dir=measurement_dir,
            )

        except Exception as e:
            logger.warning("Could not parse measurement %s: %s", measurement_dir.name, e)
            continue

    return measurements
