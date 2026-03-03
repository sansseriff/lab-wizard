from typing import Dict, List, Optional, Tuple
from pathlib import Path
import importlib
import inspect

from lab_wizard.wizard.backend.models import FilledReq, MeasurementInfo, Env, MatchingReq


def _extract_instruments_from_template(env: Env, template_file: Path, verbose: bool = False) -> List[FilledReq]:
    """Extract required instrument types from template file by importing it as a module."""
    required_instruments: List[FilledReq] = []

    if verbose:
        print("running _extract_instruments_from_template")

    # Convert file path to module name relative to lib/, then prefix with lab_wizard.lib.
    rel_path = template_file.relative_to(env.base_dir)
    if verbose:
        print("rel path is", rel_path)
    module_name = "lab_wizard.lib." + str(rel_path.with_suffix("")).replace("/", ".")
    if verbose:
        print("module name is", module_name)

    # Import the module
    module = importlib.import_module(module_name)
    if verbose:
        print("module imported:", module)

    # Find the Resources dataclass
    resources_class = None
    for name, obj in inspect.getmembers(module, inspect.isclass):
        if (
            name.endswith("Resources")
            and hasattr(obj, "__annotations__")
            and obj.__module__ == module.__name__
        ):
            resources_class = obj
            break

    if not resources_class:
        print(f"Warning: No Resources class found in {template_file}")
        return required_instruments

    # Extract type hints from the dataclass
    for field_name, field_type in resources_class.__annotations__.items():
        # Skip non-instrument fields
        if field_name in ["saver", "plotter", "params"]:
            continue

        if verbose:
            print("field name is ", field_name, "and field type is", field_type)
        required_instruments.append(
            FilledReq(
                variable_name=field_name,
                base_type=field_type,
                matching_instruments=[],
            ))

    if verbose:
        print("required instruments are", required_instruments)

    return required_instruments


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
    """Return a list of required instrument role names for a measurement."""
    measurement_dir = measurement.measurement_dir
    template_file = _template_file_for(measurement_dir)
    if verbose:
        print(f"template file is {template_file}")

    if not template_file.exists():
        print("template file does not exist")
        return []

    lib_base = _find_lib_base(template_file.parent)
    if lib_base is None:
        print("could not find lib base")
        return []

    env = Env(base_dir=lib_base)
    required = _extract_instruments_from_template(env, template_file)
    return required


def _iter_py_modules_under(package_root: Path, package_name: str) -> List[Tuple[str, Path]]:
    """List importable module names and file paths under a given package root.

    Only returns leaf modules (files) not packages themselves (except __init__ modules).
    """
    modules: List[Tuple[str, Path]] = []
    for path in package_root.rglob("*.py"):
        if path.name.startswith("__pycache__"):
            continue
        # Build module name relative to base_dir that matches Python imports
        rel = path.relative_to(package_root)
        mod_name = (package_name + "." + str(rel.with_suffix("")).replace("/", ".")).replace("..", ".")
        modules.append((mod_name, path))
    return modules


def discover_matching_instruments(env: Env, base_type: type, verbose: bool = False) -> List[MatchingReq]:
    """Discover instrument classes in lib/instruments that inherit from base_type.

    Scans the full instruments package tree so top-level instruments
    (for example keysight53220A) and nested channel classes can participate.
    """
    matches: List[MatchingReq] = []

    instruments_dir = env.instruments_dir

    package_name = "lab_wizard.lib.instruments"
    for module_name, file_path in _iter_py_modules_under(instruments_dir, package_name):
        if verbose:
            print("attempting import of ", module_name)
        try:
            module = importlib.import_module(module_name)
        except Exception as e:
            print(f"Warning: failed to import {module_name}: {e}")
            continue

        # Scan classes defined in this module
        for name, obj in inspect.getmembers(module, inspect.isclass):
            # Only consider classes defined in this module
            if obj.__module__ != module.__name__:
                continue

            # Skip private/internal classes
            if name.startswith("_"):
                continue

            # Skip explicit stand-ins/placeholders
            if getattr(obj, "ignore_in_cli", False):
                continue

            # Some files have parameter models etc.; we only care about subclasses
            try:
                if issubclass(obj, base_type) and obj is not base_type:
                    qual = f"{obj.__module__}.{obj.__qualname__}"
                    friendly = getattr(obj, "friendly_name", name)
                    matches.append(
                        MatchingReq(
                            module=module.__name__,
                            class_name=name,
                            qualname=qual,
                            file_path=file_path,
                            friendly_name=str(friendly),
                        )
                    )
            except TypeError:
                # obj is not a new-style class that can be used with issubclass
                continue

    print(f"discovered {len(matches)} matches for base type {base_type}")
    if verbose:
        print("matches are", matches)
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
            print(f"Warning: Could not parse {measurement_dir.name}: {e}")
            continue

    return measurements