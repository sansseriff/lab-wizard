from pydantic import BaseModel, Field, SerializeAsAny, model_validator
from typing import Any, List, Annotated, Literal

from lab_wizard.lib.utilities.params_discovery import load_params_class


class FileSaver(BaseModel):
    type: Literal["file_saver"] = "file_saver"
    file_path: str = Field(description="Path to save the file")
    include_timestamp: bool = Field(
        default=True, description="Include timestamp in the filename"
    )
    include_metadata: bool = Field(
        default=True, description="Include metadata in the saved file"
    )


class DatabaseSaver(BaseModel):
    type: Literal["database_saver"] = "database_saver"
    db_url: str = Field(description="Database connection URL")
    table_name: str = Field(description="Name of the table to save data")
    include_metadata: bool = Field(
        default=True, description="Include metadata in the saved data"
    )


class WebPlotter(BaseModel):
    type: Literal["web_plotter"] = "web_plotter"
    url: str = Field(description="URL of the web service to send plot data")


class MplPlotter(BaseModel):
    type: Literal["mpl_plotter"] = "mpl_plotter"
    figure_size: List[int] = Field(
        default=[10, 6], description="Size of the matplotlib figure in inches"
    )
    dpi: int = Field(default=100, description="DPI for the matplotlib figure")


class IVCurveParams(BaseModel):
    type: Literal["iv_curve"] = "iv_curve"
    start_voltage: float = Field(description="Start voltage for the IV curve")
    stop_voltage: float = Field(description="Stop voltage for the IV curve")
    step_voltage: float = Field(description="Voltage step size for the IV curve")
    num_points: int = Field(default=100, description="Number of points in the IV curve")


class PCRCurveParams(BaseModel):
    type: Literal["pcr_curve"] = "pcr_curve"
    start_voltage: float = Field(description="Start voltage for the PCR curve")
    stop_voltage: float = Field(description="Stop voltage for the PCR curve")
    step_voltage: float = Field(description="Voltage step size for the PCR curve")
    photon_rate: float = Field(description="Number of photons per second")


class Device(BaseModel):
    type: Literal["device"] = "device"
    name: str = Field(description="Name of the device")
    model: str = Field(description="Wafer")
    description: str = Field(description="Description of the device")


SaverUnion = Annotated[FileSaver | DatabaseSaver, Field(discriminator="type")]
PlotterUnion = Annotated[WebPlotter | MplPlotter, Field(discriminator="type")]
ExpUnion = Annotated[IVCurveParams | PCRCurveParams, Field(discriminator="type")]


def _parse_instrument_tree(data: dict[str, Any]) -> Any:
    """
    Recursively parse an instrument dict into the correct Params class.
    
    Uses dynamic discovery to find the right class based on the 'type' field.
    Handles nested 'children' dicts recursively.
    """
    if not isinstance(data, dict) or "type" not in data:
        return data
    
    type_str = data["type"]
    
    # Parse children recursively first
    if "children" in data and isinstance(data["children"], dict):
        parsed_children = {}
        for key, child_data in data["children"].items():
            parsed_children[key] = _parse_instrument_tree(child_data)
        data = {**data, "children": parsed_children}
    
    # Load the Params class and instantiate
    params_cls = load_params_class(type_str)
    return params_cls(**data)


class Exp(BaseModel):
    exp: ExpUnion
    device: Device
    saver: dict[str, SaverUnion]
    plotter: dict[str, PlotterUnion]
    # Instruments are loaded dynamically via params_discovery - no static union needed
    instruments: dict[str, SerializeAsAny[BaseModel]]

    @model_validator(mode="before")
    @classmethod
    def _parse_instruments_dynamically(cls, data: dict[str, Any]) -> dict[str, Any]:
        """
        Parse instruments using dynamic discovery before Pydantic validation.
        
        This allows the YAML to contain any registered instrument type without
        needing a static union that imports all Params classes upfront.
        """
        if "instruments" not in data or not isinstance(data["instruments"], dict):
            return data
        
        parsed_instruments = {}
        for key, inst_data in data["instruments"].items():
            if isinstance(inst_data, dict) and "type" in inst_data:
                parsed_instruments[key] = _parse_instrument_tree(inst_data)
            else:
                # Already parsed or not a dict - pass through
                parsed_instruments[key] = inst_data
        
        return {**data, "instruments": parsed_instruments}

    def find_all_resources(self) -> dict[str, tuple[str, object]]:
        """Find all resources in the experiment tree.
        Returns dict mapping resource_id -> (access_path, object)."""
        resources = {}

        for inst_key, instrument in self.instruments.items():
            base_path = f"exp.instruments['{inst_key}']"

            # Check if the instrument itself has a resource
            if hasattr(instrument, "has_resource") and instrument.has_resource():
                resource_id = instrument.get_resource_id()
                resources[resource_id] = (base_path, instrument)

            # Check for nested resources (e.g., in mainframes)
            if hasattr(instrument, "find_resources"):
                nested_resources = instrument.find_resources(base_path)
                for resource_id, path, obj in nested_resources:
                    resources[resource_id] = (path, obj)

        return resources

    def _generate_intermediate_variables(
        self, path: str, generated_vars: dict[str, str], counter: dict[str, int]
    ) -> tuple[str, list[str]]:
        """Generate intermediate variables for complex paths and return the final variable name and intermediate code lines."""
        parts = path.split(".")
        intermediate_lines = []

        # If it's a simple path, return as-is
        if len(parts) <= 2:  # e.g., "exp.instruments['source1']"
            return path, []

        # Build intermediate variables
        current_path = parts[0]  # Start with "exp"

        for i in range(1, len(parts) - 1):  # Skip the last part
            current_path += "." + parts[i]

            # Check if we need to create an intermediate variable
            if current_path not in generated_vars:
                # Determine the type and generate a variable name
                if "instruments[" in current_path:
                    # Extract instrument key for naming
                    import re

                    match = re.search(r"instruments\['([^']+)'\]", current_path)
                    if match:
                        inst_key = match.group(1)
                        var_name = f"{inst_key}_{counter[inst_key]}"
                        counter[inst_key] += 1

                        # Get the instrument type for the tg() call
                        inst_obj = self.instruments.get(inst_key)
                        if inst_obj:
                            type_name = type(inst_obj).__name__
                            generated_vars[current_path] = var_name
                            intermediate_lines.append(
                                f"{var_name} = tg({current_path}, {type_name})"
                            )

        # Build the final path using intermediate variables
        final_path = parts[0]
        for i in range(1, len(parts)):
            segment = "." + parts[i]
            check_path = final_path + segment

            if check_path in generated_vars and i < len(parts) - 1:
                # Replace with intermediate variable
                final_path = generated_vars[check_path]
            else:
                final_path += segment

        return final_path, intermediate_lines

    def code_generate(self, resource_mapping: dict[str, str] = None):
        """Generate code to access resources based on MyResources mapping.
        resource_mapping: dict mapping resource_id -> variable_name
        """
        if resource_mapping is None:
            resource_mapping = {}

        all_resources = self.find_all_resources()
        code_lines = []
        generated_vars: dict[str, str] = {}  # Maps path -> variable_name
        counter = {"mainframe": 1, "source": 1, "sense": 1}  # For unique naming
        all_intermediate_lines = []

        for resource_id, var_name in resource_mapping.items():
            if resource_id in all_resources:
                path, obj = all_resources[resource_id]

                # Use the helper function to generate intermediate variables
                final_path, intermediate_lines = self._generate_intermediate_variables(
                    path, generated_vars, counter
                )

                # Add intermediate lines (avoid duplicates)
                for line in intermediate_lines:
                    if line not in all_intermediate_lines:
                        all_intermediate_lines.append(line)

                # Generate the final access code
                if hasattr(obj, "generate_access_code"):
                    code_line = obj.generate_access_code(var_name, final_path)
                    code_lines.append(code_line)

        # Combine all lines
        return "\n".join(all_intermediate_lines + code_lines)
