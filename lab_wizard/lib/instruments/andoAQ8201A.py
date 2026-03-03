"""
Ando AQ8201A Optical Test System

Modern implementation of the Ando AQ8201A parent and modules
with Source interface compliance for optical attenuation control.
"""

import time
import numpy as np
from typing import Dict, Any, Tuple

from pydantic import BaseModel


from lab_wizard.lib.instruments.general.serial_inst import GPIBmodule


class AndoAQ8201AParams(BaseModel):
    """Configuration for Ando AQ8201A optical attenuator."""

    port: str
    gpib_addr: int
    slot: int
    timeout: float = 1.0
    baudrate: int = 9600
    min_attenuation: float = 0.0  # Minimum attenuation in dB
    max_attenuation: float = 60.0  # Maximum attenuation in dB
    wavelength: float = 1550.0  # Wavelength in nm


class AndoAQ8201Module(GPIBmodule):
    """
    Base class for modules in Ando AQ8201A parent

    Extends GPIBmodule to add slot-specific command formatting
    """

    def __init__(self, port: str, gpib_addr: int, slot: int, **kwargs):
        """
        Initialize Ando module

        Args:
            port: Serial port path
            gpib_addr: GPIB address of parent
            slot: Slot number in parent
            **kwargs: Additional parameters for GPIBmodule
        """
        super().__init__(port, gpib_addr, **kwargs)
        self.slot = slot

    def write(self, cmd: str) -> int:
        """
        Write command to specific slot

        Args:
            cmd: Command string

        Returns:
            Number of bytes written
        """
        slot_cmd = f"C{self.slot}\n{cmd}"
        return super().write(slot_cmd)


class AndoAQ8201_31(AndoAQ8201Module):
    """
    Ando AQ8201-31 Variable Optical Attenuator Module

    Provides wavelength-dependent optical attenuation control
    """

    def __init__(self, port: str, gpib_addr: int, slot: int, **kwargs):
        """
        Initialize AQ8201-31 attenuator module

        Args:
            port: Serial port path
            gpib_addr: GPIB address of parent
            slot: Slot number in parent
            **kwargs: Additional parameters
        """
        AndoAQ8201Module.__init__(self, port, gpib_addr, slot, **kwargs)

        # State tracking
        self.current_wavelength = None
        self.current_attenuation = None
        self.shutter_closed = False

        # Specifications
        self.wavelength_range = (1200, 1600)  # nm
        self.attenuation_range = (0, 60)  # dB
        self.attenuation_resolution = 0.05  # dB

    def connect(self) -> bool:
        """Connect to the attenuator module"""
        if super().connect():
            try:
                # Get current state
                self.get_status()
                print(f"Connected to Ando AQ8201-31 in slot {self.slot}")
                return True
            except Exception as e:
                print(f"Failed to initialize AQ8201-31: {e}")
                self.disconnect()
                return False
        return False

    def set_wavelength(self, wavelength_nm: float) -> float:
        """
        Set the operating wavelength

        Args:
            wavelength_nm: Wavelength in nanometers

        Returns:
            Actual wavelength set

        Raises:
            ValueError: If wavelength cannot be set
        """
        if not self.is_connected():
            raise RuntimeError("Attenuator not connected")

        # Clip and round wavelength to integer nm
        wavelength = int(
            np.clip(
                np.round(wavelength_nm),
                self.wavelength_range[0],
                self.wavelength_range[1],
            )
        )

        try:
            self.write(f"AW {wavelength}")
            time.sleep(0.01)

            # Verify setting
            self.get_status()
            if self.current_wavelength == wavelength:
                return float(wavelength)
            else:
                raise ValueError(
                    f"Wavelength setting failed. Expected {wavelength}nm, got {self.current_wavelength}nm"
                )

        except Exception as e:
            raise RuntimeError(f"Failed to set wavelength: {e}")

    def set_attenuation(self, attenuation_db: float) -> float:
        """
        Set optical attenuation

        Args:
            attenuation_db: Attenuation in dB

        Returns:
            Actual attenuation set

        Raises:
            ValueError: If attenuation cannot be set
        """
        if not self.is_connected():
            raise RuntimeError("Attenuator not connected")

        # Clip and round to nearest 0.05 dB
        attenuation = np.clip(
            float(attenuation_db), self.attenuation_range[0], self.attenuation_range[1]
        )
        attenuation = (
            np.round(attenuation / self.attenuation_resolution)
            * self.attenuation_resolution
        )

        try:
            self.write(f"AAV {attenuation}")
            time.sleep(0.01)

            # Verify setting
            self.get_status()
            if abs(self.current_attenuation - attenuation) < 1e-6:
                return attenuation
            else:
                raise ValueError(
                    f"Attenuation setting failed. Expected {attenuation}dB, got {self.current_attenuation}dB"
                )

        except Exception as e:
            raise RuntimeError(f"Failed to set attenuation: {e}")

    def set_shutter(self, closed: bool):
        """
        Control optical shutter

        Args:
            closed: True to close shutter, False to open
        """
        if not self.is_connected():
            raise RuntimeError("Attenuator not connected")

        try:
            self.write(f"ASHTR {int(closed)}")
            self.shutter_closed = closed
            action = "closed" if closed else "opened"
            print(f"Shutter {action}")

        except Exception as e:
            raise RuntimeError(f"Failed to control shutter: {e}")

    def close_shutters(self):
        """Close optical shutter"""
        self.set_shutter(True)

    def open_shutters(self):
        """Open optical shutter"""
        self.set_shutter(False)

    def get_status(self) -> Tuple[int, float]:
        """
        Get current wavelength and attenuation

        Returns:
            Tuple of (wavelength_nm, attenuation_db)
        """
        if not self.is_connected():
            raise RuntimeError("Attenuator not connected")

        try:
            response = self.query("AD?")
            params = response.split()

            # Parse response format: "WAVELENGTH_XXXX_nm attenuation_value"
            self.current_wavelength = int(
                params[0][6:10]
            )  # Extract from "WAVELENGTH_XXXX_nm"
            self.current_attenuation = float(params[1])

            return (self.current_wavelength, self.current_attenuation)

        except Exception as e:
            raise RuntimeError(f"Failed to get status: {e}")

    def set_output(self, value: float):
        """

        Args:
            value: Attenuation in dB
        """
        return self.set_attenuation(value)

    def get_output(self) -> float:
        """

        Returns:
            Current attenuation in dB
        """
        self.get_status()
        return self.current_attenuation

    def enable_output(self):
        """Enable output (open shutter)"""
        self.open_shutters()

    def disable_output(self):
        """Disable output (close shutter)"""
        self.close_shutters()

    def is_output_enabled(self) -> bool:
        """Check if output is enabled (shutter open)"""
        return not self.shutter_closed

    def get_range(self) -> tuple:
        """Get attenuation range"""
        return self.attenuation_range

    def get_info(self) -> Dict[str, Any]:
        """Get instrument information"""
        info = {
            "instrument_type": "Ando AQ8201-31 Variable Optical Attenuator",
            "slot": self.slot,
            "connected": self.is_connected(),
            "wavelength_range_nm": self.wavelength_range,
            "attenuation_range_db": self.attenuation_range,
            "attenuation_resolution_db": self.attenuation_resolution,
        }

        if self.is_connected():
            try:
                wavelength, attenuation = self.get_status()
                info.update(
                    {
                        "current_wavelength_nm": wavelength,
                        "current_attenuation_db": attenuation,
                        "shutter_closed": self.shutter_closed,
                    }
                )
            except Exception:
                pass

        return info


class AndoAQ8201_412(AndoAQ8201Module):
    """
    Ando AQ8201-412 Optical Switch Module

    Provides optical path switching functionality
    """

    def __init__(self, port: str, gpib_addr: int, slot: int, **kwargs):
        """
        Initialize AQ8201-412 switch module

        Args:
            port: Serial port path
            gpib_addr: GPIB address of parent
            slot: Slot number in parent
            **kwargs: Additional parameters
        """
        super().__init__(port, gpib_addr, slot, **kwargs)

        self.current_switch = None
        self.current_position = None

    def connect(self) -> bool:
        """Connect to the switch module"""
        if super().connect():
            print(f"Connected to Ando AQ8201-412 in slot {self.slot}")
            return True
        return False

    def set_switch(self, switch: str):
        """
        Select switch A or B

        Args:
            switch: 'A' or 'B'
        """
        if not self.is_connected():
            raise RuntimeError("Switch not connected")

        switch = str(switch).upper()
        if switch not in ["A", "B"]:
            raise ValueError("Switch must be 'A' or 'B'")

        try:
            cmd = "D1" if switch == "A" else "D2"
            self.write(cmd)
            self.current_switch = switch
            print(f"Selected switch {switch}")

        except Exception as e:
            raise RuntimeError(f"Failed to set switch: {e}")

    def set_position(self, position: int):
        """
        Set switch position

        Args:
            position: 1 or 2
        """
        if not self.is_connected():
            raise RuntimeError("Switch not connected")

        if position not in [1, 2]:
            raise ValueError("Position must be 1 or 2")

        try:
            cmd = "SA1SB1" if position == 1 else "SA1SB2"
            self.write(cmd)
            self.current_position = position
            print(f"Set position to {position}")

        except Exception as e:
            raise RuntimeError(f"Failed to set position: {e}")

    def get_info(self) -> Dict[str, Any]:
        """Get switch information"""
        return {
            "instrument_type": "Ando AQ8201-412 Optical Switch",
            "slot": self.slot,
            "connected": self.is_connected(),
            "current_switch": self.current_switch,
            "current_position": self.current_position,
        }


class AndoAQ8201A:
    """
    Ando AQ8201A Parent Controller

    Manages multiple modules in the Ando optical test system
    """

    def __init__(self, port: str, gpib_addr: int, **kwargs):
        """
        Initialize AQ8201A parent

        Args:
            port: Serial port path
            gpib_addr: GPIB address
            **kwargs: Additional parameters
        """
        self.port = port
        self.gpib_addr = gpib_addr
        self.modules = {}
        self.kwargs = kwargs

    def add_attenuator(self, slot: int, **kwargs) -> AndoAQ8201_31:
        """
        Add AQ8201-31 attenuator module

        Args:
            slot: Slot number
            **kwargs: Additional parameters

        Returns:
            Attenuator module instance
        """
        module_kwargs = {**self.kwargs, **kwargs}
        attenuator = AndoAQ8201_31(self.port, self.gpib_addr, slot, **module_kwargs)
        self.modules[f"attenuator_{slot}"] = attenuator
        return attenuator

    def add_switch(self, slot: int, **kwargs) -> AndoAQ8201_412:
        """
        Add AQ8201-412 switch module

        Args:
            slot: Slot number
            **kwargs: Additional parameters

        Returns:
            Switch module instance
        """
        module_kwargs = {**self.kwargs, **kwargs}
        switch = AndoAQ8201_412(self.port, self.gpib_addr, slot, **module_kwargs)
        self.modules[f"switch_{slot}"] = switch
        return switch

    def connect_all(self) -> bool:
        """Connect to all modules"""
        success = True
        for name, module in self.modules.items():
            try:
                if not module.connect():
                    print(f"Failed to connect to {name}")
                    success = False
            except Exception as e:
                print(f"Error connecting to {name}: {e}")
                success = False

        return success

    def disconnect_all(self):
        """Disconnect from all modules"""
        for name, module in self.modules.items():
            try:
                module.disconnect()
            except Exception as e:
                print(f"Error disconnecting from {name}: {e}")

    def get_info(self) -> Dict[str, Any]:
        """Get parent information"""
        return {
            "instrument_type": "Ando AQ8201A Optical Test System",
            "port": self.port,
            "gpib_address": self.gpib_addr,
            "modules": {
                name: module.get_info() for name, module in self.modules.items()
            },
        }


def main():
    """Example usage"""
    print("Ando AQ8201A Optical Test System")
    print("Example usage:")
    print()
    print("# Create parent")
    print("parent = AndoAQ8201A('/dev/ttyUSB0', gpib_addr=1)")
    print()
    print("# Add attenuator in slot 3")
    print("attenuator = parent.add_attenuator(slot=3)")
    print()
    print("# Connect and use")
    print("attenuator.connect()")
    print("attenuator.set_wavelength(1550)")
    print("attenuator.set_attenuation(10.0)")


if __name__ == "__main__":
    main()
