#!/usr/bin/env python3
"""
setup_measurement.py - CLI tool for setting up SNSPD measurement experiments

This tool provides an interactive CLI for:
1. Selecting measurement types
2. Choosing compatible instruments
3. Copying necessary files to a working directory
4. Generating measurement configuration

Author: Generated for SNSPD Library Restructure
Date: June 2025
"""

from __future__ import annotations

import os
import sys
import shutil
import importlib
import inspect
import argparse
import yaml
from pathlib import Path
from datetime import datetime
from typing import Dict, Type, List, Any, Optional
from dataclasses import dataclass
from abc import ABC, abstractmethod


# Import the actual base classes from the instrument modules
from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.parent import Parent


@dataclass
class MeasurementInfo:
    """Information about available measurements"""

    name: str
    description: str
    required_instruments: Dict[str, Type[Any]]
    measurement_dir: Path


@dataclass
class ParentResource:
    class_name: str
    class_obj: Type[Any] | None = None
    params_obj: Type[Any] | None = None
    parent: ParentResource | None = None


@dataclass
class InstrumentInfo:
    """Information about available instruments"""

    display_name: str
    class_name: str
    module_name: str
    file_path: Path
    class_obj: Type[Any]
    params_obj: Type[Any] | None = None
    parent: ParentResource | None = None


class MeasurementSetup:
    """Main CLI class for setting up measurements"""

    def __init__(self):
        self.base_dir = Path(__file__).parent / "lib"
        self.instruments_dir = self.base_dir / "instruments"
        self.measurements_dir = self.base_dir / "measurements"
        self.projects_dir = self.base_dir / "projects"

        # Ensure projects directory exists
        self.projects_dir.mkdir(exist_ok=True)

        # Add the new library to Python path
        sys.path.insert(0, str(self.base_dir))

    def discover_instruments(
        self, instrument_dir: Path, base_class: Type
    ) -> Dict[str, InstrumentInfo]:
        """Discover instruments that inherit from the given base class."""
        instruments = {}
        errors = []

        # Look for Python files in the directory and subdirectories
        for py_file in instrument_dir.rglob("*.py"):
            if py_file.name.startswith("__") or py_file.name in [
                "visaInst.py",
                "serialInst.py",
            ]:
                continue

            try:
                # Convert file path to module name
                rel_path = py_file.relative_to(self.base_dir)
                module_name = str(rel_path.with_suffix("")).replace("/", ".")

                # Import the module
                module = importlib.import_module(module_name)

                # Find subclasses in this module
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(obj, base_class)
                        and obj != base_class
                        and obj.__module__ == module.__name__
                    ):
                        display_name = getattr(obj, "DISPLAY_NAME", name)

                        instruments[display_name] = InstrumentInfo(
                            display_name=display_name,
                            class_name=name,
                            module_name=module_name,
                            file_path=py_file,
                            class_obj=obj,
                        )

            except Exception as e:
                error_msg = f"Could not load {py_file.name}: {str(e)}"
                errors.append(error_msg)
                print(f"Warning: {error_msg}")

        if errors:
            print(f"\n⚠️  {len(errors)} instrument(s) failed to load.")
            print("Other instruments are still available.\n")

        return instruments

    def discover_measurements(self) -> Dict[str, MeasurementInfo]:
        """Discover available measurement types."""
        measurements = {}

        for measurement_dir in self.measurements_dir.iterdir():
            if not measurement_dir.is_dir() or measurement_dir.name.startswith("__"):
                continue

            # Look for the main measurement file
            measurement_file = measurement_dir / f"{measurement_dir.name}.py"
            if not measurement_file.exists():
                continue

            # Look for the template file to analyze required instruments
            template_file = (
                measurement_dir / f"{measurement_dir.name}_setup_template.py"
            )

            try:
                required_instruments = {}
                description = f"{measurement_dir.name} measurement"

                if template_file.exists():
                    # Parse the template file to extract required instruments
                    # Try to import the template directly like we do with instruments
                    required_instruments = self._extract_instruments_from_template(
                        template_file
                    )

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
                    required_instruments=required_instruments,
                    measurement_dir=measurement_dir,
                )

            except Exception as e:
                print(f"Warning: Could not parse {measurement_dir.name}: {e}")
                continue

        return measurements

    def select_instrument(
        self, instrument_type: str, available_instruments: Dict[str, InstrumentInfo]
    ) -> InstrumentInfo:
        """Interactive instrument selection."""
        print(f"\nAvailable {instrument_type}s:")

        instruments_list = list(available_instruments.items())
        for i, (display_name, info) in enumerate(instruments_list, 1):
            # Show description from class docstring if available
            if info.class_obj and hasattr(info.class_obj, "__doc__"):
                doc = info.class_obj.__doc__
                doc_line = doc.split("\n")[0] if doc else "No description"
            else:
                doc_line = "No description"
            print(f"{i}. {display_name} - {doc_line}")

        while True:
            try:
                choice = (
                    int(
                        input(
                            f"\nChoose {instrument_type} (1-{len(instruments_list)}): "
                        )
                    )
                    - 1
                )
                if 0 <= choice < len(instruments_list):
                    break
                print("Invalid choice, try again.")
            except (ValueError, KeyboardInterrupt):
                print("Please enter a number or Ctrl+C to exit.")

        return instruments_list[choice][1]

    def setup_measurement(self, measurement_name: str = None):
        """Main setup workflow."""
        print("🔬 SNSPD Measurement Setup Tool")
        print("=" * 40)

        # Discover available measurements
        measurements = self.discover_measurements()

        if not measurements:
            print("❌ No measurements found in the measurements directory.")
            return

        # Select measurement if not provided
        if measurement_name and measurement_name in measurements:
            selected_measurement = measurements[measurement_name]
        else:
            print("\nAvailable measurements:")
            measurement_list: list[tuple[str, MeasurementInfo]] = list(
                measurements.items()
            )
            for i, (name, info) in enumerate(measurement_list, 1):
                print(f"{i}. {name} - {info.description}")

            while True:
                try:
                    choice = (
                        int(
                            input(f"\nChoose measurement (1-{len(measurement_list)}): ")
                        )
                        - 1
                    )
                    if 0 <= choice < len(measurement_list):
                        break
                    print("Invalid choice, try again.")
                except (ValueError, KeyboardInterrupt):
                    print("Please enter a number or Ctrl+C to exit.")
                    return

            selected_measurement: MeasurementInfo = measurement_list[choice][1]

        print(f"\n📋 Setting up: {selected_measurement.name}")
        print(f"Description: {selected_measurement.description}")
        print(f"required instruments: {selected_measurement.required_instruments}")

        # Discover and select instruments
        selected_instruments: dict[str, InstrumentInfo] = {}
        for role, base_class in selected_measurement.required_instruments.items():
            print(f"\n🔍 Discovering {role} instruments...")
            available = self.discover_instruments(self.instruments_dir, base_class)

            if not available:
                print(f"❌ No compatible {role} instruments found.")
                return

            selected = self.select_instrument(role, available)

            if selected.class_obj.parent_class:
                # parent_class is a string like "lib.instruments.sim900.Sim900"
                # we need to import it and set it as the parent_class of the selected instrument

                parent_module = importlib.import_module(
                    selected.class_obj.parent_class.split(".")[:-1]
                )

                parent_class = getattr(
                    parent_module, selected.class_obj.parent_class.split(".")[-1]
                )

                selected.parent = ParentResource(
                    class_name=parent_class.__name__, class_obj=parent_class
                )

            selected_instruments[role] = selected

        # for each instrument in selected_instruments, check if it can be created based on the lab status

        # look in the lib/config directory for a file with name selected_instruments[role].class_name.lower() + ".yml"
        for role, instrument_info in selected_instruments.items():
            yaml_path = (
                self.base_dir / "config" / f"{instrument_info.class_name.lower()}.yml"
            )
            if yaml_path.exists():
                instrument_info.yaml_path = yaml_path
            else:
                # error out
                print(
                    f"❌ No configuration file found for {instrument_info.class_name}. "
                    f"Please create a YAML file in the config directory with the name {instrument_info.class_name.lower()}.yml"
                )
                return

            # load the yaml file and throw it into the

    def combine_yaml_files(
        self,
        selected_measurement: MeasurementInfo,
        selected_instruments: Dict[str, InstrumentInfo],
        project_dir: Path,
    ):
        """Combine measurement and instrument YAML files into a complete configuration."""

        # Load measurement YAML
        measurement_yaml_path = (
            selected_measurement.measurement_dir / f"{selected_measurement.name}.yml"
        )
        combined_config = {}

        if measurement_yaml_path.exists():
            with open(measurement_yaml_path, "r") as f:
                combined_config = yaml.safe_load(f) or {}

        # Add instruments section
        combined_config["instruments"] = {}

        # Track parents and their required sub-instruments
        parents = {}
        standalone_instruments = {}

        for role, instrument_info in selected_instruments.items():
            if instrument_info.yaml_path and instrument_info.yaml_path.exists():
                with open(instrument_info.yaml_path, "r") as f:
                    instrument_config = yaml.safe_load(f) or {}

                # Check if this is a SRS sub-instrument
                if (
                    instrument_info.class_name.startswith("SIM")
                    and instrument_info.class_name != "SIM900"
                ):
                    # This is a sub-instrument, add it to parent
                    parent_key = "sim900"

                    if parent_key not in parents:
                        # Initialize parent with base config
                        if "sim900" in instrument_config:
                            parents[parent_key] = instrument_config["sim900"].copy()
                            parents[parent_key]["sub-instruments"] = []
                        else:
                            # Default parent config if not found
                            parents[parent_key] = {
                                "port": "/dev/ttyUSB0",
                                "gpibAddr": 2,
                                "sub-instruments": [],
                            }

                    # Add this specific sub-instrument
                    sub_instrument_name = instrument_info.class_name.lower()
                    if sub_instrument_name in instrument_config:
                        sub_config = {
                            sub_instrument_name: instrument_config[sub_instrument_name]
                        }
                        parents[parent_key]["sub-instruments"].append(sub_config)
                    else:
                        print(
                            f"Warning: {sub_instrument_name} config not found in YAML"
                        )

                else:
                    # Standalone instrument (like Keysight counter)

                    for instrument_name, config in instrument_config.items():
                        # More flexible matching for instrument names
                        if (
                            instrument_name.lower()
                            == instrument_info.class_name.lower()
                            or instrument_name.lower().replace("_", "").replace("-", "")
                            == instrument_info.class_name.lower()
                            .replace("_", "")
                            .replace("-", "")
                        ):
                            standalone_instruments[instrument_name] = config
                            break
                    else:
                        # If no exact match found, try the first instrument in the YAML
                        if len(instrument_config) == 1:
                            instrument_name, config = next(
                                iter(instrument_config.items())
                            )
                            standalone_instruments[instrument_name] = config

        # Add parents to combined config
        for parent_name, parent_config in parents.items():
            combined_config["instruments"][parent_name] = parent_config

        # Add standalone instruments
        for instrument_name, config in standalone_instruments.items():
            combined_config["instruments"][instrument_name] = config

        # Save combined configuration
        output_path = project_dir / f"{selected_measurement.name}_complete.yml"
        with open(output_path, "w") as f:
            yaml.dump(combined_config, f, default_flow_style=False, indent=2)

        print(f"✓ Generated {output_path.name}")
        return output_path

    def _extract_instruments_from_template(
        self, template_file: Path
    ) -> Dict[str, Type[Any]]:
        """Extract required instrument types from template file by importing it as a module."""
        required_instruments = {}

        try:
            # Convert file path to module name, just like in discover_instruments()
            rel_path = template_file.relative_to(self.base_dir)
            module_name = str(rel_path.with_suffix("")).replace("/", ".")

            # Import the module
            module = importlib.import_module(module_name)

            # Find the Resources dataclass
            '''
            Looking for something like:

            @dataclass
            class IVCurveResources(YamlClass):
                """Pure measurement parameters for IV curve measurements"""

                saver: GenericSaver
                plotter: GenericPlotter
                voltage_source: VSource
                voltage_sense: VSense
                params: IVCurveParams
            
            '''
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
                    # TODO: come back to these later
                    continue

                required_instruments[field_name] = field_type

        except Exception as e:
            print(f"Warning: Failed to import template as module: {e}")

        return required_instruments

    def _copy_instrument_dependencies(
        self, target_dir: Path, selected_instruments: Dict[str, InstrumentInfo]
    ):
        """Copy base classes and dependencies for selected instruments."""
        copied_files = set()

        def copy_class_dependencies(class_obj: Type, file_path: Path):
            """Recursively copy dependencies for a class."""
            if file_path in copied_files:
                return

            # Get all base classes
            for base_class in inspect.getmro(class_obj)[1:]:  # Skip the class itself
                if base_class.__module__ == "builtins":
                    continue

                # Convert module name to file path
                module_parts = base_class.__module__.split(".")
                if module_parts[0] == "instruments":
                    # This is one of our instrument modules
                    base_file_path = self.instruments_dir
                    for part in module_parts[1:]:
                        base_file_path = base_file_path / part
                    base_file_path = base_file_path.with_suffix(".py")

                    if base_file_path.exists() and base_file_path not in copied_files:
                        # Create subdirectory structure if needed
                        rel_path = base_file_path.relative_to(self.instruments_dir)
                        target_file = target_dir / rel_path
                        target_file.parent.mkdir(parents=True, exist_ok=True)

                        shutil.copy2(base_file_path, target_file)
                        copied_files.add(base_file_path)
                        print(f"✓ Copied dependency {rel_path}")

                        # Recursively copy dependencies of this base class
                        try:
                            copy_class_dependencies(base_class, base_file_path)
                        except Exception:
                            pass  # Skip if we can't analyze this class

        # Copy dependencies for each selected instrument
        for instrument_info in selected_instruments.values():
            copy_class_dependencies(
                instrument_info.class_obj, instrument_info.file_path
            )

        # Also copy common __init__.py files
        for root, dirs, files in os.walk(self.instruments_dir):
            if "__init__.py" in files:
                init_file = Path(root) / "__init__.py"
                rel_path = init_file.relative_to(self.instruments_dir)
                target_file = target_dir / rel_path
                target_file.parent.mkdir(parents=True, exist_ok=True)
                if not target_file.exists():
                    shutil.copy2(init_file, target_file)


def find_dict_by_nested_attribute(instruments: str, attribute_name: str):
    """
    Recursively searches through the instruments list to find the instrument
    with a nested attribute matching the given attribute_name.
    """
    for instrument in instruments:
        if isinstance(instrument, dict):
            for key, value in instrument.items():
                if key == "attribute_name" and value == attribute_name:
                    return instrument
                elif isinstance(value, (list, dict)):
                    result = find_dict_by_nested_attribute(value, attribute_name)
                    if result:
                        return result
    return None


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Setup SNSPD measurement experiments")
    parser.add_argument("measurement", nargs="?", help="Measurement type to setup")

    args = parser.parse_args()

    setup = MeasurementSetup()

    try:
        setup.setup_measurement(args.measurement)
    except KeyboardInterrupt:
        print("\n\n👋 Setup cancelled by user.")
    except Exception as e:
        print(f"\n❌ Error during setup: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
