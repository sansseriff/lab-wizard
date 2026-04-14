"""
genericSource.py
Author: SNSPD Library Rewrite
Date: June 4, 2025

Abstract base class for source instruments (voltage/current sources, signal generators, etc.)
"""

from abc import ABC, abstractmethod


class VSource(ABC):
    """
    Abstract base class for all source instruments.

    Source instruments include voltage sources, current sources, signal
    generators, etc. Lifetime follows RAII: connection happens in the
    constructor and the underlying transport (e.g. LocalSerialDep,
    LocalVisaDep) releases its handle automatically via its own __del__
    and atexit hooks. Subclasses do not implement their own disconnect.
    """

    @abstractmethod
    def set_voltage(self, voltage: float) -> bool:
        """
        Set the output voltage of the source.

        Args:
            voltage: The output voltage to set

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    def turn_on(self) -> bool:
        """
        Turn on the output of the source.

        Returns:
            bool: True if successful, False otherwise
        """
        pass

    @abstractmethod
    def turn_off(self) -> bool:
        """
        Turn off the output of the source.

        Returns:
            bool: True if successful, False otherwise
        """
        pass


class StandInVSource(VSource):
    """
    Stand-in class for VSource.
    This class can be used for testing or as a placeholder when no actual instrument is available.
    """

    ignore_in_cli = True

    def __init__(self):
        self.voltage = 0.0
        self.output_enabled = False
        print("Stand-in voltage source initialized.")

    def set_voltage(self, voltage: float) -> bool:
        """Set the voltage (stand-in behavior)."""
        print(f"Stand-in: Setting voltage to {voltage}V")
        self.voltage = voltage
        return True

    def turn_on(self) -> bool:
        """Turn on the output (stand-in behavior)."""
        print(f"Stand-in: Turning on output")
        self.output_enabled = True
        return True

    def turn_off(self) -> bool:
        """Turn off the output (stand-in behavior)."""
        print(f"Stand-in: Turning off output")
        self.output_enabled = False
        return True
