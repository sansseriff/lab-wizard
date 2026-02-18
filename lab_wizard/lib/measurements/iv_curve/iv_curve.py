"""
ivCurve.py
Author: SNSPD Library Rewrite
Date: June 4, 2025

IV Curve measurement module for SNSPDs.
Updated for the new architecture with automatic instrument discovery and type-safe configuration.

Based on the original ivCurve.py from Alex Walter.
"""

import time
import traceback
import numpy as np
from typing import TYPE_CHECKING, Dict, Any, Generator, Optional
from dataclasses import dataclass

from lab_wizard.lib.utilities.plotter import Plotter

from lab_wizard.lib.instruments.general.vsource import VSource
from lab_wizard.lib.instruments.general.vsense import VSense

from lab_wizard.lib.measurements.general.genericMeasurement import GenericMeasurement

if TYPE_CHECKING:
    from iv_curve_setup_template import IVCurveResources, IVCurveParams



class IVCurveMeasurement(GenericMeasurement):
    """
    IV Curve measurement class for SNSPDs.

    This class handles the complete IV curve measurement process including
    voltage sourcing, current measurement, data collection, and plotting.
    """

    def __init__(self, resources: "IVCurveResources"):
        """
        Initialize IV curve measurement.

        Args:
            params: Measurement configuration parameters
            voltage_source: Voltage source instrument (must implement GenericSource)
            voltage_sense: Voltage sensing instrument (must implement GenericSense)
        """
        self.params = resources.params
        self.voltage_source = resources.voltage_source
        self.voltage_sense = resources.voltage_sense

        # Data storage
        self.bias_voltages = []
        self.sense_voltages = []
        self.currents = []

    def voltage_generator(self) -> Generator[float, None, None]:
        """
        Generator for bias voltages.

        Yields:
            float: Next bias voltage to apply
        """
        for voltage in self.params.voltage_sequence():
            yield voltage

    def connect_instruments(self) -> bool:
        """
        Connect to all instruments.

        Returns:
            bool: True if all connections successful
        """
        try:
            print("Connecting to voltage source...")
            source_connected = self.voltage_source.connect()

            print("Connecting to voltage sense...")
            sense_connected = self.voltage_sense.connect()

            if source_connected and sense_connected:
                print("All instruments connected successfully")
                return True
            else:
                print("Failed to connect to one or more instruments")
                return False

        except Exception as e:
            print(f"Error connecting to instruments: {e}")
            return False

    def disconnect_instruments(self) -> bool:
        """
        Disconnect from all instruments.

        Returns:
            bool: True if all disconnections successful
        """
        success = True

        try:
            # Turn off source before disconnecting
            if hasattr(self.voltage_source, "enable_output"):
                self.voltage_source.enable_output(False)
            elif hasattr(self.voltage_source, "turn_off"):
                self.voltage_source.turn_off()
        except Exception as e:
            print(f"Error turning off voltage source: {e}")
            success = False

        try:
            self.voltage_source.disconnect()
        except Exception as e:
            print(f"Error disconnecting voltage source: {e}")
            success = False

        try:
            self.voltage_sense.disconnect()
        except Exception as e:
            print(f"Error disconnecting voltage sense: {e}")
            success = False

        return success

    def setup_instruments(self) -> bool:
        """
        Configure instruments for IV curve measurement.

        Returns:
            bool: True if setup successful
        """
        try:
            # Enable voltage source output
            if hasattr(self.voltage_source, "enable_output"):
                self.voltage_source.enable_output(True)
            elif hasattr(self.voltage_source, "turn_on"):
                self.voltage_source.turn_on()

            # Set initial voltage to zero
            self.voltage_source.set_output(0.0)
            time.sleep(0.01)

            # Configure voltage sense if needed
            if hasattr(self.voltage_sense, "configure_measurement"):
                self.voltage_sense.configure_measurement("DC voltage")

            return True

        except Exception as e:
            print(f"Error setting up instruments: {e}")
            return False

    def setup_plotting(self) -> None:
        """
        Initialize real-time plotting.
        """
        if not self.params.enable_plotting:
            return

        self.voltage_plotter.setup_plot(
            title="Voltage Curve", xlabel="Bias Voltage (V)", ylabel="Sense Voltage (V)"
        )

        self.current_plotter.setup_plot(
            title="IV Curve", xlabel="Sense Voltage (V)", ylabel="Bias Current (μA)"
        )

    def take_measurement_point(self, bias_voltage: float) -> tuple[float, float]:
        """
        Take a single measurement point.

        Args:
            bias_voltage: Bias voltage to apply

        Returns:
            tuple: (sense_voltage, current) in volts and microamps
        """
        # Set bias voltage
        self.voltage_source.set_output(bias_voltage)

        # Wait for settling
        time.sleep(self.params.settling_time)

        # Measure sense voltage
        sense_voltage = self.voltage_sense.measure()

        # Calculate current through bias resistor
        voltage_across_resistor = bias_voltage - sense_voltage
        current_microamps = 1e6 * voltage_across_resistor / self.params.bias_resistance

        return sense_voltage, current_microamps

    def update_plots(
        self, bias_voltage: float, sense_voltage: float, current: float
    ) -> None:
        """
        Update real-time plots with new data point.

        Args:
            bias_voltage: Applied bias voltage
            sense_voltage: Measured sense voltage
            current: Calculated current
        """
        if not self.params.enable_plotting:
            return

        self.voltage_plotter.add_point(bias_voltage, sense_voltage)
        self.current_plotter.add_point(sense_voltage, current)

    def run_measurement(self) -> Dict[str, Any]:
        """
        Execute the complete IV curve measurement.

        Returns:
            Dict[str, Any]: Measurement results and metadata
        """
        start_time = time.time()

        # Setup
        if not self.connect_instruments():
            raise RuntimeError("Failed to connect to instruments")

        if not self.setup_instruments():
            self.disconnect_instruments()
            raise RuntimeError("Failed to setup instruments")

        if self.params.enable_plotting:
            self.setup_plotting()

        try:
            print("Starting IV curve measurement...")

            # Main measurement loop
            for i, bias_voltage in enumerate(self.voltage_generator()):
                try:
                    sense_voltage, current = self.take_measurement_point(bias_voltage)

                    # Store data
                    self.bias_voltages.append(bias_voltage)
                    self.sense_voltages.append(sense_voltage)
                    self.currents.append(current)

                    # Update plots
                    self.update_plots(bias_voltage, sense_voltage, current)

                    # Optional: Print progress
                    if i % 50 == 0:
                        print(
                            f"Point {i + 1}: V_bias={bias_voltage:.3f}V, V_sense={sense_voltage:.3f}V, I={current:.1f}μA"
                        )

                except Exception as e:
                    print(f"Error at measurement point {i}: {e}")
                    # Continue with next point
                    continue

            end_time = time.time()
            measurement_time = end_time - start_time

            print(f"Measurement completed in {measurement_time:.1f} seconds")

            # Prepare results
            results = {
                "bias_voltages": np.array(self.bias_voltages),
                "sense_voltages": np.array(self.sense_voltages),
                "currents": np.array(self.currents),
                "measurement_time": measurement_time,
                "parameters": self.params,
            }

            # Save data
            if self.params.save_data:
                self.data_handler.save_data(results)

            return results

        except Exception as e:
            print(f"Error during measurement: {e}")
            traceback.print_exc()
            raise

        finally:
            # Cleanup
            self.disconnect_instruments()

            if self.params.enable_plotting:
                self.voltage_plotter.show()
                self.current_plotter.show()

    def analyze_results(self, results: Dict[str, Any]) -> Dict[str, Any]:
        """
        Perform basic analysis on IV curve results.

        Args:
            results: Measurement results from run_measurement()

        Returns:
            Dict[str, Any]: Analysis results
        """
        bias_v = results["bias_voltages"]
        sense_v = results["sense_voltages"]
        currents = results["currents"]

        analysis = {
            "max_bias_voltage": np.max(np.abs(bias_v)),
            "max_sense_voltage": np.max(np.abs(sense_v)),
            "max_current": np.max(np.abs(currents)),
            "resistance_at_max_bias": np.abs(sense_v[np.argmax(np.abs(bias_v))])
            / np.abs(currents[np.argmax(np.abs(bias_v))])
            * 1e6,  # in ohms
            "num_points": len(bias_v),
        }

        return analysis
