"""
counter.py
Author: SNSPD Library Rewrite
Date: June 4, 2025

Abstract base class for counter instruments.
"""

from abc import ABC, abstractmethod


class Counter(ABC):
    """
    Abstract base class for counter instruments.

    Lifetime follows RAII: connection happens in the constructor and the
    underlying transport (e.g. LocalVisaDep) releases its handle via its
    own __del__/atexit. Subclasses do not implement their own disconnect.
    """

    @abstractmethod
    def count(self, gate_time: float = 1.0, channel: int | None = None) -> int:
        """
        Count events for a specified gate time.

        Args:
            gate_time: Gate time in seconds
            channel: Optional channel number for multi-channel instruments

        Returns:
            int: Number of counts
        """
        pass

    @abstractmethod
    def set_gate_time(self, gate_time: float, channel: int | None = None) -> bool:
        """
        Set the gate time for counting.

        Args:
            gate_time: Gate time in seconds
            channel: Optional channel number for multi-channel instruments

        Returns:
            bool: True if successful, False otherwise
        """
        pass


class StandInCounter(Counter):
    """
    Stand-in class for Counter.
    This class is used when the actual counter instrument is not available.
    It provides default implementations for all methods.
    """

    ignore_in_cli = True

    def __init__(self):
        self.gate_time = 1.0
        print("Stand-in counter instrument initialized.")

    def count(self, gate_time: float = 1.0, channel: int | None = None) -> int:
        channel_str = f" on channel {channel}" if channel is not None else ""
        print(f"Stand-in: Counting for {gate_time}s{channel_str}")
        return 0  # Default count value

    def set_gate_time(self, gate_time: float, channel: int | None = None) -> bool:
        channel_str = f" on channel {channel}" if channel is not None else ""
        print(f"Stand-in: Setting gate time to {gate_time}s{channel_str}")
        self.gate_time = gate_time
        return True
