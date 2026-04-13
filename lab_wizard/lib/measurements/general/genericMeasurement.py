from abc import ABC, abstractmethod
from typing import Any


class GenericMeasurement(ABC):
    @abstractmethod
    def run_measurement(self) -> dict[str, Any]:
        """
        Run the measurement process.

        This method should be implemented by subclasses to define the specific measurement logic.
        """
        pass
