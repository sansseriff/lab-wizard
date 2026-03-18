# Plan: Migrate yoAQ2212 and andoAQ8201A to the lab_wizard instrument system

## Context

Two legacy instrument files exist as flat files with their own ad-hoc communication and class hierarchies:

- `lab_wizard/lib/instruments/yoAQ2212.py` — Yokogawa AQ2212 Ethernet mainframe (laser, attenuator, switch, power meter modules)
- `lab_wizard/lib/instruments/andoAQ8201A.py` — Ando AQ8201A GPIB mainframe (attenuator, switch modules), accessed via Prologix GPIB controller

These must be refactored into the standard lab_wizard parent-child instrument system using Pydantic Params classes, scoped dependency objects, and params_discovery-compatible file layouts. The old flat files are deleted after migration.

---

## Reference Patterns

| Instrument          | Pattern                                                                    | Key comm layer                                          |
| ------------------- | -------------------------------------------------------------------------- | ------------------------------------------------------- |
| **YokoGawa AQ2212** | Like `dbay` — IP-connected top-level parent                                | VISA TCP socket via `LocalVisaDep`                      |
| **Ando AQ8201A**    | Like `sim900` — GPIB child of PrologixGPIB, parent of its own slot modules | `PrologixAddressedInstrumentDep` → `AndoAQ8201ASlotDep` |

Critical reference files to read before implementing:

- `lab_wizard/lib/instruments/dbay/dbay.py` — DBay/DBayParams pattern
- `lab_wizard/lib/instruments/dbay/modules/dac4d.py` — slot-child Params + instrument
- `lab_wizard/lib/instruments/sim900/sim900.py` — hybrid parent+child Params + instrument
- `lab_wizard/lib/instruments/sim900/modules/sim928.py` — GPIB slot-module pattern
- `lab_wizard/lib/instruments/general/prologix_gpib.py` — PrologixChildParams union (must be updated)
- `lab_wizard/lib/instruments/general/parent_child.py` — all base classes
- `lab_wizard/lib/instruments/general/visa.py` — LocalVisaDep for VISA TCP comm
- `lab_wizard/lib/instruments/sim900/comm.py` — Sim900SlotDep (template for slot-scoped comm)

---

## Part 1: YokoGawa AQ2212

### File Layout

```
lab_wizard/lib/instruments/yokogawaAQ2212/
  __init__.py                  (empty)
  yokogawaAQ2212.py            (Params class + YokoGawaAQ2212 instrument)
  comm.py                      (YokoAQ2212Dep, YokoAQ2212SlotDep)
  deps.py                      (re-export alias)
  modules/
    __init__.py                (empty)
    laser.py
    attenuator.py
    switch.py
    power_meter.py
```

### comm.py

The Yokogawa AQ2212 communicates via VISA TCP socket (SCPI over TCP). The original code used `visaInst` with an IP address and port.

```python
from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.visa import LocalVisaDep

class YokoAQ2212Dep(Dependency):
    """Frame-level VISA TCP comm for YokoGawa AQ2212."""
    def __init__(self, ip_address: str, ip_port: int, *, offline: bool = False):
        self.offline = offline
        if not offline:
            resource = f"TCPIP0::{ip_address}::{ip_port}::SOCKET"
            self._visa = LocalVisaDep(resource)
            self._visa.connect()

    def write(self, cmd: str) -> None:
        if self.offline: return
        self._visa.write(cmd)

    def query(self, cmd: str) -> str:
        if self.offline: return ""
        return self._visa.query(cmd)

    def slot(self, slot: int) -> "YokoAQ2212SlotDep":
        return YokoAQ2212SlotDep(self, slot)


class YokoAQ2212SlotDep(Dependency):
    """Slot-scoped dep: all commands include the slot number in SCPI path."""
    def __init__(self, frame: YokoAQ2212Dep, slot: int):
        self._frame = frame
        self.slot = slot
        self.offline = frame.offline

    def write(self, cmd: str) -> None:
        # Commands already include slot via caller (e.g. "SOUR{slot}:...")
        self._frame.write(cmd)

    def query(self, cmd: str) -> str:
        return self._frame.query(cmd)
```

Note: SCPI commands for AQ2212 modules already embed the slot number inline (e.g., `SOUR1:FREQ?`, `INP2:ATT?`), so SlotDep passes commands through verbatim, providing offline gating and a clean interface for module classes.

### deps.py

```python
from lab_wizard.lib.instruments.yokogawaAQ2212.comm import YokoAQ2212Dep, YokoAQ2212SlotDep
__all__ = ["YokoAQ2212Dep", "YokoAQ2212SlotDep"]
```

### YokoGawaAQ2212Params (in yokogawaAQ2212.py)

```python
from typing import Annotated, Literal
from pydantic import Field
from lab_wizard.lib.instruments.general.parent_child import (
    IPLike, ParentParams, CanInstantiate
)

YokoAQ2212ChildParams = Annotated[
    LaserParams | AttenuatorParams | SwitchParams | PowerMeterParams,
    Field(discriminator="type")
]

class YokoGawaAQ2212Params(
    IPLike,
    ParentParams["YokoGawaAQ2212", YokoAQ2212Dep, YokoAQ2212ChildParams],
    CanInstantiate["YokoGawaAQ2212"],
):
    type: Literal["yokogawa_aq2212"] = "yokogawa_aq2212"
    offline: bool = False
    children: dict[str, YokoAQ2212ChildParams] = Field(default_factory=dict)

    @property
    def inst(self):
        return YokoGawaAQ2212

    def create_inst(self) -> "YokoGawaAQ2212":
        return YokoGawaAQ2212.from_params(self)
```

Key: `ip_address` (default `"10.7.0.13"`) and `ip_port` (default `50000`) come from `IPLike`.

### YokoGawaAQ2212 instrument class (in yokogawaAQ2212.py)

```python
class YokoGawaAQ2212(
    Parent[YokoAQ2212Dep, YokoAQ2212ChildParams],
    ParentFactory["YokoGawaAQ2212Params", "YokoGawaAQ2212"],
):
    def __init__(self, ip_address: str, ip_port: int, params: YokoGawaAQ2212Params | None = None):
        self.comm = YokoAQ2212Dep(ip_address, ip_port, offline=getattr(params, "offline", False))
        self.children: dict[str, Child] = {}
        self.params = params or YokoGawaAQ2212Params()

    @property
    def dep(self) -> YokoAQ2212Dep:
        return self.comm

    @classmethod
    def from_params(cls, params: YokoGawaAQ2212Params) -> "YokoGawaAQ2212":
        return cls(params.ip_address, params.ip_port, params)

    def make_child(self, key: str) -> Child:
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot_dep = self.dep.slot(int(params.slot))
        if isinstance(params, LaserParams):
            child = Laser(slot_dep, params)
        elif isinstance(params, AttenuatorParams):
            child = Attenuator(slot_dep, params)
        elif isinstance(params, SwitchParams):
            child = Switch(slot_dep, params)
        else:
            child = PowerMeter(slot_dep, params)
        self.children[key] = child
        return child
```

### Module Params pattern (e.g., attenuator.py)

Each module uses `SlotLike` + `ChildParams`, following exactly the dac4d.py pattern:

```python
class AttenuatorParams(SlotLike, ChildParams["Attenuator"]):
    type: Literal["yoko_attenuator"] = "yoko_attenuator"
    attribute_name: str = ""
    offline: bool = False
    wavelength_nm: float = 1550.0

    @property
    def inst(self):
        return Attenuator

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212.YokoGawaAQ2212"
```

### Module instrument class pattern (e.g., attenuator.py)

```python
class Attenuator(Child[YokoAQ2212SlotDep, AttenuatorParams]):
    def __init__(self, dep: YokoAQ2212SlotDep, params: AttenuatorParams):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212.YokoGawaAQ2212"

    # --- Translated from old yoAQ2212_Attenuator ---
    def get_attenuation(self) -> float:
        return float(self._dep.query(f"INP{self.slot}:ATT?"))

    def set_attenuation(self, atten_db: float) -> None:
        self._dep.write(f"INP{self.slot}:ATT {atten_db}")

    def get_wavelength_nm(self) -> float:
        return float(self._dep.query(f"INP{self.slot}:WAV?")) * 1e9

    def set_wavelength_nm(self, wav_nm: float) -> None:
        self._dep.write(f"INP{self.slot}:WAV +{wav_nm}E-009")

    def set_output(self, enabled: bool) -> None:
        self._dep.write(f"OUTP{self.slot}:STAT {int(enabled)}")

    def get_output_status(self) -> bool:
        return bool(int(self._dep.query(f"OUTP{self.slot}:STAT?")))
```

Type strings for module discriminators:

- `"yoko_laser"`, `"yoko_attenuator"`, `"yoko_switch"`, `"yoko_power_meter"`

### Method mapping from old → new (yoAQ2212)

| Old class + method                         | New class + method                  |
| ------------------------------------------ | ----------------------------------- |
| `yoAQ2212_frame_controller.set_date()`     | `YokoGawaAQ2212.set_date()`         |
| `yoAQ2212_laser.getLaserStatus()`          | `Laser.get_status()`                |
| `yoAQ2212_laser.setLaserOUT(True/False)`   | `Laser.set_output(bool)`            |
| `yoAQ2212_laser.getLaserFreqWav()`         | `Laser.get_frequency_wavelength()`  |
| `yoAQ2212_laser.setLaserFreqWav(wav=1550)` | `Laser.set_wavelength_nm(1550)`     |
| `yoAQ2212_laser.getLaserPow()`             | `Laser.get_power_dbm()`             |
| `yoAQ2212_laser.setLaserPow(dBm=10)`       | `Laser.set_power_dbm(10)`           |
| `yoAQ2212_Attenuator.getAtten()`           | `Attenuator.get_attenuation()`      |
| `yoAQ2212_Attenuator.setAtten(val)`        | `Attenuator.set_attenuation(val)`   |
| `yoAQ2212_Attenuator.getAttenWav()`        | `Attenuator.get_wavelength_nm()`    |
| `yoAQ2212_Attenuator.setAttenWav(wav)`     | `Attenuator.set_wavelength_nm(wav)` |
| `yoAQ2212_Attenuator.setAttenOUT(bool)`    | `Attenuator.set_output(bool)`       |
| `yoAQ2212_Switch.getSwitchStat(dev)`       | `Switch.get_status(dev)`            |
| `yoAQ2212_Switch.setSwitch(pos, dev)`      | `Switch.set_position(pos, dev)`     |
| `yoAQ2212_PowerMeter.getPowerMeas()`       | `PowerMeter.get_power_fetch_dbm()`  |
| `yoAQ2212_PowerMeter.getPowerMeasSing()`   | `PowerMeter.get_power_read_w()`     |
| `yoAQ2212_PowerMeter.setMeasAvg(t)`        | `PowerMeter.set_averaging_time(t)`  |
| `yoAQ2212_PowerMeter.setMeasWav(wav)`      | `PowerMeter.set_wavelength_nm(wav)` |

---

## Part 2: Ando AQ8201A

### File Layout

```
lab_wizard/lib/instruments/andoAQ8201A/
  __init__.py                  (empty)
  andoAQ8201A.py               (Params class + AndoAQ8201A instrument, hybrid parent+child like sim900)
  comm.py                      (AndoAQ8201ADep, AndoAQ8201ASlotDep)
  deps.py                      (re-export alias)
  modules/
    __init__.py                (empty)
    attenuator31.py            (Attenuator31Params + Attenuator31, from AndoAQ8201_31)
    switch412.py               (Switch412Params + Switch412, from AndoAQ8201_412)
```

### comm.py

The Ando AQ8201A is accessed via a Prologix serial-GPIB controller (same as Sim900). The Prologix parent creates a `PrologixAddressedInstrumentDep` and passes it as the dep to `AndoAQ8201A`.

Commands to slot modules are prefixed: `C{slot}\n{cmd}` (matching old `write()` in `AndoAQ8201Module`).

```python
from lab_wizard.lib.instruments.general.parent_child import Dependency
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep

AndoAQ8201ADep = PrologixAddressedInstrumentDep  # type alias: same dep as other GPIB instruments

class AndoAQ8201ASlotDep(Dependency):
    """Slot-scoped comm: prefixes all commands with C{slot}\n as Ando protocol requires."""
    def __init__(self, gpib_comm: PrologixAddressedInstrumentDep, slot: int, *, offline: bool = False):
        self._gpib = gpib_comm
        self.slot = slot
        self.offline = offline

    def write(self, cmd: str) -> None:
        if self.offline: return
        self._gpib.write(f"C{self.slot}\n{cmd}")

    def query(self, cmd: str) -> str:
        if self.offline: return ""
        self._gpib.write(f"C{self.slot}\n{cmd}")
        return self._gpib.read()
```

### deps.py

```python
from lab_wizard.lib.instruments.andoAQ8201A.comm import AndoAQ8201ASlotDep
from lab_wizard.lib.instruments.general.prologix_comm import PrologixAddressedInstrumentDep as AndoAQ8201ADep
__all__ = ["AndoAQ8201ADep", "AndoAQ8201ASlotDep"]
```

### AndoAQ8201AParams (hybrid parent + child, like Sim900)

```python
from typing import Annotated, Literal
from pydantic import Field
from lab_wizard.lib.instruments.general.parent_child import (
    GPIBAddressLike, ParentParams, ChildParams
)

AndoAQ8201AChildParams = Annotated[
    Attenuator31Params | Switch412Params,
    Field(discriminator="type")
]

class AndoAQ8201AParams(
    GPIBAddressLike,
    ParentParams["AndoAQ8201A", AndoAQ8201ADep, AndoAQ8201AChildParams],
    ChildParams["AndoAQ8201A"],
):
    type: Literal["ando_aq8201a"] = "ando_aq8201a"
    offline: bool = False
    children: dict[str, AndoAQ8201AChildParams] = Field(default_factory=dict)

    @property
    def inst(self):
        return AndoAQ8201A

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"
```

Key: `gpib_address: str = "0"` comes from `GPIBAddressLike`.

### AndoAQ8201A instrument class (hybrid parent + child, like Sim900)

```python
class AndoAQ8201A(
    Parent[AndoAQ8201ADep, AndoAQ8201AChildParams],
    Child[Any, Any],
):
    def __init__(self, dep: AndoAQ8201ADep, params: AndoAQ8201AParams):
        self.params = params
        self._dep = dep
        self.children: dict[str, Child] = {}

    @property
    def dep(self) -> AndoAQ8201ADep:
        return self._dep

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.general.prologix_gpib.PrologixGPIB"

    def make_child(self, key: str) -> Child:
        if key in self.children:
            return self.children[key]
        params = self.params.children[key]
        slot_dep = AndoAQ8201ASlotDep(
            self._dep, int(params.slot),
            offline=bool(getattr(params, "offline", False))
        )
        if isinstance(params, Attenuator31Params):
            child = Attenuator31(slot_dep, params)
        else:
            child = Switch412(slot_dep, params)
        self.children[key] = child
        return child
```

### Module Params pattern (e.g., attenuator31.py)

```python
class Attenuator31Params(SlotLike, ChildParams["Attenuator31"]):
    type: Literal["ando_attenuator31"] = "ando_attenuator31"
    attribute_name: str = ""
    offline: bool = False
    min_attenuation: float = 0.0
    max_attenuation: float = 60.0
    wavelength_nm: float = 1550.0

    @property
    def inst(self):
        return Attenuator31

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A.AndoAQ8201A"
```

### Module instrument class (e.g., attenuator31.py)

```python
class Attenuator31(Child[AndoAQ8201ASlotDep, Attenuator31Params]):
    """Ando AQ8201-31 Variable Optical Attenuator Module."""
    def __init__(self, dep: AndoAQ8201ASlotDep, params: Attenuator31Params):
        self._dep = dep
        self.params = params
        self.slot = dep.slot

    @property
    def parent_class(self) -> str:
        return "lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A.AndoAQ8201A"

    def get_status(self) -> tuple[int, float]:
        """Returns (wavelength_nm, attenuation_db)."""
        response = self._dep.query("AD?")
        parts = response.split()
        wavelength = int(parts[0][6:10])
        attenuation = float(parts[1])
        return wavelength, attenuation

    def set_wavelength_nm(self, wavelength_nm: float) -> None:
        wav = int(round(wavelength_nm))
        self._dep.write(f"AW {wav}")

    def set_attenuation_db(self, attenuation_db: float) -> None:
        self._dep.write(f"AAV {attenuation_db}")

    def open_shutter(self) -> None:
        self._dep.write("ASHTR 0")

    def close_shutter(self) -> None:
        self._dep.write("ASHTR 1")
```

Switch412 similarly translates from `AndoAQ8201_412`.

### Method mapping from old → new (andoAQ8201A)

| Old class + method                     | New class + method                                 |
| -------------------------------------- | -------------------------------------------------- |
| `AndoAQ8201_31.set_wavelength(wav_nm)` | `Attenuator31.set_wavelength_nm(wav_nm)`           |
| `AndoAQ8201_31.set_attenuation(db)`    | `Attenuator31.set_attenuation_db(db)`              |
| `AndoAQ8201_31.set_shutter(closed)`    | `Attenuator31.close_shutter()` / `.open_shutter()` |
| `AndoAQ8201_31.get_status()`           | `Attenuator31.get_status()`                        |
| `AndoAQ8201_31.enable_output()`        | `Attenuator31.open_shutter()`                      |
| `AndoAQ8201_31.disable_output()`       | `Attenuator31.close_shutter()`                     |
| `AndoAQ8201_412.set_switch(switch)`    | `Switch412.set_switch(switch)`                     |
| `AndoAQ8201_412.set_position(pos)`     | `Switch412.set_position(pos)`                      |

---

## Part 3: Files to Modify (beyond new files)

### 1. `lab_wizard/lib/instruments/general/prologix_gpib.py`

Add `AndoAQ8201AParams` to the `PrologixChildParams` union (currently only contains `Sim900Params`):

```python
# Before:
PrologixChildParams = Annotated[Sim900Params, Field(discriminator="type")]

# After:
from lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A import AndoAQ8201AParams

PrologixChildParams = Annotated[
    Sim900Params | AndoAQ8201AParams,
    Field(discriminator="type")
]
```

Also update `PrologixGPIB.make_child()` to handle `AndoAQ8201AParams`:

```python
def make_child(self, key: str) -> Child[Any, Any]:
    child_params = self.params.children[key]
    if isinstance(child_params, Sim900Params):
        mainframe_dep = Sim900MainframeDep(self._dep.addressed(int(child_params.gpib_address)))
        child = Sim900(mainframe_dep, child_params)
    elif isinstance(child_params, AndoAQ8201AParams):
        gpib_dep = self._dep.addressed(int(child_params.gpib_address))
        child = AndoAQ8201A(gpib_dep, child_params)
    else:
        raise TypeError(f"Unknown child type: {type(child_params)}")
    self.children[key] = child
    return child
```

### 2. Delete legacy files

- `lab_wizard/lib/instruments/yoAQ2212.py` — deleted (replaced by new folder)
- `lab_wizard/lib/instruments/andoAQ8201A.py` — deleted (replaced by new folder)

---

## Part 4: params_discovery compatibility

The discovery system (in `lab_wizard/lib/utilities/params_discovery.py`) will automatically find the new classes if:

1. Files are placed under `lab_wizard/lib/instruments/` (✓)
2. Files are NOT named `comm.py`, `deps.py`, `state.py`, or `__init__.py` (✓ — those are in skip list)
3. Each Params class has `type: Literal["..."]` annotation (✓)
4. Each Params class inherits from `CanInstantiate` or `ChildParams` (✓)
5. Each child Params class has a `parent_class` property returning the fully-qualified parent path (✓)

No manual registration required. The disk cache is automatically invalidated when the new files are created.

---

## YAML tree example after migration

```yaml
instruments:
  # YokoGawa AQ2212 (top-level, IP-addressed)
  a1b2c3d4:
    type: yokogawa_aq2212
    ip_address: 10.7.0.13
    ip_port: 50000
    offline: false
    children:
      e5f6a7b8:
        type: yoko_laser
        slot: "1"
        attribute_name: ""
      c9d0e1f2:
        type: yoko_attenuator
        slot: "2"
        wavelength_nm: 1550.0
        attribute_name: ""

  # PrologixGPIB (top-level, USB-addressed) — with Ando as GPIB child
  87847ad5:
    type: prologix_gpib
    port: /dev/ttyUSB0
    children:
      deadbeef:
        type: ando_aq8201a
        gpib_address: "1"
        children:
          11223344:
            type: ando_attenuator31
            slot: "3"
            wavelength_nm: 1550.0
          55667788:
            type: ando_switch412
            slot: "5"
```

---

## Implementation Order

1. Create `yokogawaAQ2212/` folder and files (comm.py, deps.py, modules/, yokogawaAQ2212.py)
2. Create `andoAQ8201A/` folder and files (comm.py, deps.py, modules/, andoAQ8201A.py)
3. Update `prologix_gpib.py` to add `AndoAQ8201AParams` to union and `make_child`
4. Delete `yoAQ2212.py` and `andoAQ8201A.py` legacy files

---

## Verification

```bash
# 1. Import check — both modules must be importable
python -c "
from lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212 import YokoGawaAQ2212Params, YokoGawaAQ2212
from lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A import AndoAQ8201AParams, AndoAQ8201A
print('Imports OK')
"

# 2. Params discovery — types must be discoverable
python -c "
from lab_wizard.lib.utilities.params_discovery import load_params_class
p = load_params_class('yokogawa_aq2212')
print('yokogawa_aq2212:', p)
p = load_params_class('yoko_attenuator')
print('yoko_attenuator:', p)
p = load_params_class('ando_aq8201a')
print('ando_aq8201a:', p)
p = load_params_class('ando_attenuator31')
print('ando_attenuator31:', p)
"

# 3. Params instantiation — defaults must be valid
python -c "
from lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212 import YokoGawaAQ2212Params
from lab_wizard.lib.instruments.andoAQ8201A.andoAQ8201A import AndoAQ8201AParams
from lab_wizard.lib.instruments.andoAQ8201A.modules.attenuator31 import Attenuator31Params
print(YokoGawaAQ2212Params())
print(AndoAQ8201AParams())
print(Attenuator31Params(slot='3'))
"

# 4. Config tree test — must serialize/deserialize via config_io
python -c "
from lab_wizard.lib.utilities.config_io import model_to_commented_map
from lab_wizard.lib.instruments.yokogawaAQ2212.yokogawaAQ2212 import YokoGawaAQ2212Params
p = YokoGawaAQ2212Params(ip_address='10.7.0.13', ip_port=50000)
cm = model_to_commented_map(p, exclude_fields=('children',))
print(list(cm.keys()))  # should be: ['type', 'ip_address', 'ip_port', ...]
"

# 5. Run existing tests
python -m pytest tests/test_config_io.py -x -q
```
