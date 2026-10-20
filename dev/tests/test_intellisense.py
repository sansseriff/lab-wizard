from lab_wizard.lib.instruments.general.prologix_gpib import PrologixGPIBParams
from lab_wizard.lib.instruments.sim900.modules.sim928 import Sim928Params
from lab_wizard.lib.instruments.sim900.sim900 import Sim900Params
from lab_wizard.lib.instruments.dbay.dbay import DBayParams
from lab_wizard.lib.instruments.dbay.modules.dac4d import Dac4DParams
from lab_wizard.lib.instruments.sim900.modules.sim970 import Sim970Params
from lab_wizard.lib.utilities.config_io import instrument_hash

# Build params-first, then instantiate via create_inst() + make_child()

_sim928_key = instrument_hash("sim928", "1")
_sim900_key = instrument_hash("sim900", "3")

_sim900_params = Sim900Params(gpib_address="3", children={_sim928_key: Sim928Params(gpib_address="1")})
_prologix_params = PrologixGPIBParams(port="FAKE", children={_sim900_key: _sim900_params})

try:
    _prologix = _prologix_params.create_inst()
    _sim900 = _prologix.make_child(_sim900_key)
    sim928 = _sim900.make_child(_sim928_key)
    sim928.set_voltage(3.0)
except Exception:
    pass


_sim970_key = instrument_hash("sim970", "5")
_sim900_for_970_params = Sim900Params(gpib_address="3", children={_sim970_key: Sim970Params(gpib_address="5")})
_prologix_for_970_params = PrologixGPIBParams(port="FAKE", children={_sim900_key: _sim900_for_970_params})

try:
    sim970_chain = (
        _prologix_for_970_params.create_inst()
        .make_child(_sim900_key)
        .make_child(_sim970_key)
    )
    # sim970_chain.get_channel(3).get_voltage() - disabled: mock doesn't return valid voltage
except Exception:
    pass


# After refactor, adding a Dac4D module materializes its channels from params
_dac4d_key = instrument_hash("dac4D", "1")
_dbay_params = DBayParams(ip_address="FAKE", children={_dac4d_key: Dac4DParams(slot="1")})

try:
    dac4d_module = _dbay_params.create_inst().make_child(_dac4d_key)
    dac4d_module.get_channel(0).set_voltage(1.0)  # type: ignore[attr-defined]
except Exception:
    pass

try:
    dac4d_module.get_channel(0).set_voltage(1)  # type: ignore[attr-defined]
except Exception:
    pass
