from unittest.mock import MagicMock, patch

from lab_wizard.lib.instruments.dbay.dbay import DBay
from lab_wizard.lib.instruments.dbay.modules.dac4d import Dac4D
from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIB
from lab_wizard.lib.instruments.keysight53220A import Keysight53220A
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970
from lab_wizard.lib.instruments.sim900.sim900 import Sim900
from lab_wizard.lib.utilities.model_tree import Exp


def _exp_with_sim_and_counter() -> Exp:
    return Exp.model_validate(
        {
            "exp": {"type": "pcr_curve"},
            "device": {
                "type": "device",
                "name": "demo",
                "model": "unknown",
                "description": "demo",
            },
            "saver": {
                "default": {
                    "type": "file_saver",
                    "file_path": "out.csv",
                    "include_timestamp": True,
                    "include_metadata": True,
                }
            },
            "plotter": {"default": {"type": "mpl_plotter", "figure_size": [8, 6], "dpi": 100}},
            "instruments": {
                "/dev/ttyUSB0": {
                    "type": "prologix_gpib",
                    "port": "/dev/ttyUSB0",
                    "baudrate": 9600,
                    "timeout": 1,
                    "children": {
                        "3": {
                            "type": "sim900",
                            "gpib_address": "3",
                            "children": {
                                "1": {"type": "sim928", "slot": "1", "offline": False, "settling_time": 0.4},
                                "5": {
                                    "type": "sim970",
                                    "slot": "5",
                                    "offline": False,
                                    "attribute_name": "sense",
                                    "channels": [{}, {}, {}, {}],
                                },
                            },
                        }
                    },
                },
                "10.0.0.5:5025": {
                    "type": "keysight53220A",
                    "ip_address": "10.0.0.5",
                    "ip_port": 5025,
                    "offline": True,
                    "ext_trigger": False,
                    "channels": [{}, {}],
                },
            },
        }
    )


def _exp_with_dbay() -> Exp:
    return Exp.model_validate(
        {
            "exp": {"type": "iv_curve"},
            "device": {
                "type": "device",
                "name": "demo",
                "model": "unknown",
                "description": "demo",
            },
            "saver": {
                "default": {
                    "type": "file_saver",
                    "file_path": "out.csv",
                    "include_timestamp": True,
                    "include_metadata": True,
                }
            },
            "plotter": {"default": {"type": "mpl_plotter", "figure_size": [8, 6], "dpi": 100}},
            "instruments": {
                "10.0.0.6:8345": {
                    "type": "dbay",
                    "ip_address": "10.0.0.6",
                    "ip_port": 8345,
                    "children": {
                        "1": {"type": "dac4D", "slot": "1", "name": "Dac4D", "channels": [{}, {}, {}, {}]}
                    },
                }
            },
        }
    )


def test_sim_from_config_reuses_existing_instances() -> None:
    exp = _exp_with_sim_and_counter()

    prologix = PrologixGPIB.from_config(exp, key="/dev/ttyUSB0")
    sim900_a = Sim900.from_config(prologix, key="3")
    sim900_b = Sim900.from_config(prologix, key="3")
    assert sim900_a is sim900_b

    sim928_a = Sim928.from_config(sim900_a, key="1")
    sim928_b = Sim928.from_config(sim900_a, key="1")
    assert sim928_a is sim928_b


def test_sim970_channels_keep_module_slot_transport() -> None:
    exp = _exp_with_sim_and_counter()

    prologix = PrologixGPIB.from_config(exp, key="/dev/ttyUSB0")
    sim900 = Sim900.from_config(prologix, key="3")
    sim970 = Sim970.from_config(sim900, key="5")

    assert sim970.slot == 5
    assert sim970.channels
    assert all(ch.slot == 5 for ch in sim970.channels)


def test_dbay_and_keysight_from_config() -> None:
    dbay_exp = _exp_with_dbay()

    mock_module = MagicMock()
    mock_client = MagicMock()
    mock_client.module.return_value = mock_module

    with patch("lab_wizard.lib.instruments.dbay.dbay.DBayClient", return_value=mock_client):
        dbay = DBay.from_config(dbay_exp, key="10.0.0.6:8345")
        dac_a = Dac4D.from_config(dbay, key="1")
        dac_b = Dac4D.from_config(dbay, key="1")
        assert dac_a is dac_b
        assert len(dac_a.channels) == 4

    sim_exp = _exp_with_sim_and_counter()
    keysight = Keysight53220A.from_config(sim_exp, key="10.0.0.5:5025")
    assert len(keysight.channels) == 2
