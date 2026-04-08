import types
import sys
import pytest

from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.utilities.config_io import instrument_hash


@pytest.fixture(autouse=True)
def patch_serial(monkeypatch: pytest.MonkeyPatch):
    """Provide a fake serial.Serial so no real hardware is touched."""

    class FakeSerial:
        def __init__(self, *_, **__):  # type: ignore[no-untyped-def]
            self.is_open = True

        def close(self):
            self.is_open = False

        def flush(self):
            pass

        def write(self, data: bytes):
            return len(data)

        def readline(self):
            return b""

    # Patch both the imported 'serial' module and the already imported symbol inside our package
    fake_module = types.SimpleNamespace(Serial=FakeSerial)
    monkeypatch.setitem(sys.modules, "serial", fake_module)
    import lab_wizard.lib.instruments.general.serial as serial_mod

    serial_mod.serial = fake_module


def test_requested_chain_expression():
    # Build params first with children pre-configured
    sim928_key = instrument_hash("sim928", "1")
    sim970_key = instrument_hash("sim970", "5")
    sim900_key = instrument_hash("sim900", "3")

    sim900_params = Sim900Params(
        gpib_address="3",
        children={
            sim928_key: Sim928Params(slot="1"),
            sim970_key: Sim970Params(slot="5"),
        },
    )
    controller_params = PrologixGPIBParams(
        port="FAKE",
        children={sim900_key: sim900_params},
    )

    controller = controller_params.create_inst()
    sim900 = controller.make_child(sim900_key)
    sim928 = sim900.make_child(sim928_key)
    _sim970 = sim900.make_child(sim970_key)

    sim928.set_voltage(3.0)

    # sim970 is a grandchild (child of sim900), not directly in controller.children
    assert controller.children.get(sim970_key) is None
    assert controller.children.get(sim900_key) is sim900
    assert sim900.children.get(sim928_key) is sim928

    print(sim900.children)
