# Instrument API Styles — Comparison

A design discussion comparing approaches for the public API used to initialize instruments
in wizard-generated project files. The goal is to find a style that is readable to Python
novices, keeps YAML as the single source of truth for settings, and is simple to generate.

---

## Current generated code (for reference)

```python
from typing import cast

prologix_raw = exp.instruments['/dev/ttyUSB0']
if not isinstance(prologix_raw, PrologixGPIBParams):
    raise TypeError("Expected PrologixGPIBParams at exp.instruments['/dev/ttyUSB0']")
prologix_p = prologix_raw
prologix_i = prologix_p.create_inst()

sim900_p = cast(Sim900Params, prologix_p.children['4'])
sim900_i = prologix_i.add_child(sim900_p, '4')

sim928_p = cast(Sim928Params, sim900_p.children['1'])
sim928_i = sim900_i.add_child(sim928_p, '1')

keysight53220a_raw = exp.instruments['10.7.0.3:8888']
if not isinstance(keysight53220a_raw, Keysight53220AParams):
    raise TypeError("Expected Keysight53220AParams at exp.instruments['10.7.0.3:8888']")
keysight53220a_p = keysight53220a_raw
keysight53220a_i = keysight53220a_p.create_inst()

voltage_source_1 = sim928_i
counter_1 = keysight53220a_i.channels[1]
```

All settings come from YAML. The `_p` / `_i` suffix convention tracks params vs. live
instruments. `add_child` and `create_inst` are the public API. `isinstance` / `cast` are
needed for type-safety around the `exp.instruments` dict. The instrument hierarchy
(PrologixGPIB → Sim900 → Sim928) is encoded through successive `add_child` calls.

---

## Style A — `from_config` everywhere

```python
prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
sim900   = Sim900.from_config(prologix, '4')
sim928   = Sim928.from_config(sim900, '1')
keysight = Keysight53220A.from_config(exp, '10.7.0.3:8888')

voltage_source = sim928
counter        = keysight.channels[1]
```

**How it works:** `from_config` on a root instrument looks up `exp.instruments[key]`,
validates the type, and constructs the instrument from those params. `from_config` on a
child instrument looks up `parent.params.children[key]`, extracts the comm/dep from the
parent, and constructs the child. All settings come from YAML — nothing is baked into the
Python file except identity keys.

**Pros:**
- Uniform pattern at every level — one concept to learn
- Explicit class name on every line (self-documenting type, good IDE autocomplete)
- YAML is single source of truth — no drift between Python file and YAML
- Simple for the code generator: only needs class name, parent variable, and key
- No `_p`/`_i` pairs, no `isinstance`/`cast`, no `add_child`

**Cons:**
- Hides *why* the parent is passed — is it for communication, config, or both?
- `'4'` and `'1'` are opaque strings — no indication that `'4'` is a GPIB address and `'1'` is a slot
- A novice can't easily see or override settings without understanding the params tree

---

## Style B — Hybrid (from_config for roots, navigation for children)

```python
prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
sim900   = prologix.gpib(4)
sim928   = sim900.slot(1)
keysight = Keysight53220A.from_config(exp, '10.7.0.3:8888')

voltage_source = sim928
counter        = keysight.channel(1)
```

**How it works:** Roots are constructed from config as in Style A. Children are constructed
via navigation methods on the parent (`.gpib()`, `.slot()`, `.channel()`). Each method reads
`self.params.children[key]`, determines the child type from the discriminator, builds the
appropriate dep/comm, and returns a fully constructed child. Settings come from YAML.

**Pros:**
- Reads like a physical wiring diagram — `prologix.gpib(4)` is immediately meaningful
- Consistent with pymeasure's adapter pattern (`adapter.gpib(9)`)
- Method names (`gpib`, `slot`) explain the connection type
- Most concise of all styles

**Cons:**
- Return type of `.gpib(4)` is ambiguous statically — IDE can't know it returns `Sim900`
  without complex overloads or explicit type annotations on the variable
- Two different patterns in the same block (classmethod for roots, instance method for
  children) — asymmetric
- Every parent instrument needs named addressing methods — extra API surface per instrument
- Generator needs per-instrument metadata (which method name to emit)
- Without config loaded (interactive use), these methods have nothing to look up

---

## Style C — Explicit comm and params

```python
prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
sim900   = Sim900.from_config(
    comm=prologix.gpib_comm('4'),
    params=prologix.child_params['4'],
)
sim928   = Sim928.from_config(
    comm=sim900.slot_comm('1'),
    params=sim900.child_params['1'],
)
keysight = Keysight53220A.from_config(exp, '10.7.0.3:8888')

voltage_source = sim928
counter        = keysight.channels[1]
```

**How it works:** Roots are the same as Style A. Children receive both arguments explicitly:
the comm object (scoped to the address) and the params object (from the parent's config
tree). The child's `from_config` uses both to construct itself. Settings come from YAML via
the params object.

**Pros:**
- Transparent about the two purposes of the parent dependency: communication (`comm=`)
  and configuration (`params=`)
- A user can inspect or mutate the params object before passing it in (hackable)
- `prologix.gpib_comm('4')` explains the connection type in the method name
- `child_params['1']` is just dict access — familiar to any Python user

**Cons:**
- The key appears twice per child (`'4'` in both `gpib_comm('4')` and `child_params['4']`)
- More verbose — 3 lines per child instead of 1
- Exposes `comm` as a concept that novices then need to understand ("what is a comm?")
- `child_params` is still a Pydantic model underneath — novices poking at it will encounter
  BaseModel behavior
- Generator needs to know the comm method name per instrument (same issue as Style B)

---

## Style D — Named-keyword `connect`

```python
prologix = PrologixGPIB.connect(exp, port='/dev/ttyUSB0')
sim900   = Sim900.connect(prologix, gpib='4')
sim928   = Sim928.connect(sim900, slot='1')
keysight = Keysight53220A.connect(exp, address='10.7.0.3:8888')

voltage_source = sim928
counter        = keysight.channels[1]
```

**How it works:** Identical mechanics to Style A internally. The difference is naming:
`connect` instead of `from_config`, and the key is passed as a named keyword argument
whose name describes the connection type (`port=`, `gpib=`, `slot=`, `address=`). Each
instrument class declares its keyword name as a `ClassVar`. Settings come from YAML.

For interactive/notebook use without YAML, the same classes accept explicit settings:

```python
prologix = PrologixGPIB(port='/dev/ttyUSB0', baudrate=9600)
sim900   = Sim900(prologix, gpib='4')
sim928   = Sim928(sim900, slot='1', settling_time=0.4)
```

**Pros:**
- Each line is self-documenting: class name (what), parent (through what), keyword (how)
- `slot='1'` and `gpib='4'` explain the addressing scheme without extra method names
- Uniform pattern — same `connect` call at all levels
- The word "connect" is intuitive — matches the physical action
- YAML is source of truth; hackable by mutating `parent.params.children[key]` before calling `connect`
- Generator only needs class name, parent variable, key value, and keyword name (a `ClassVar`)

**Cons:**
- Still hides the comm/params distinction (same as Style A, by design)
- The keyword name is one more `ClassVar` per instrument class
- `connect` as a classmethod name is less standard than `from_config` (though arguably
  more intuitive)

---

## Style E — Full explicit init (portable, no YAML at runtime)

A variant the wizard can optionally generate where all settings are baked directly into
the constructor calls. No `exp` object, no YAML loaded at runtime. The file is fully
self-contained.

```python
# Values extracted from YAML at project generation time.
# To change a setting, edit the value here AND update the project YAML to keep
# the metadata record consistent, or re-run the wizard to regenerate this file.
prologix = PrologixGPIB(port='/dev/ttyUSB0', baudrate=9600)
sim900   = Sim900(prologix, gpib='4')
sim928   = Sim928(sim900, slot='1', settling_time=0.4)
keysight = Keysight53220A(ip_address='10.7.0.3', ip_port=8888)

voltage_source = sim928
counter        = keysight.channels[1]
```

**How it works:** The generator extracts every non-key, non-internal field from the Params
tree at generation time and emits them as explicit named kwargs. No `**kwargs` spread — each
field is named individually so the signature is fully visible. The YAML is still generated
and saved alongside the data as the metadata record, but the Python file does not load it.
Uses the same `__init__` as the interactive API — this style IS the interactive API, just
emitted by the generator with all values filled in from YAML.

**Pros:**
- Completely transparent — every setting is visible in the file, no hidden config
- No runtime dependency on the YAML or `exp` object
- Portable — the Python file works standalone, even without the project directory
- Easiest style for a novice to understand and hand-edit
- Instrument `__init__` signatures use plain named kwargs — no `BaseModel`, no classmethods

**Cons:**
- **Drift risk**: if a user edits a value in this file, the YAML and Python are out of sync.
  The YAML remains the metadata record, but it no longer matches what was actually run.
- The generator must emit every settings field by name, not as a spread — requires knowing
  which Params fields are "user settings" vs. internal (`type`, `enabled`, `children`)
- If many settings have non-default values, constructor calls become long
- Re-running the wizard to regenerate this file will overwrite any hand-edits

**When to use:** Notebooks, one-off scripts, sharing a setup with a colleague who doesn't
have access to the project YAML, or any case where portability matters more than metadata
consistency.

---

## Side-by-side

```
CURRENT:
  prologix_i = prologix_p.create_inst()
  sim900_i   = prologix_i.add_child(sim900_p, '4')
  sim928_i   = sim900_i.add_child(sim928_p, '1')

STYLE A (from_config):
  prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
  sim900   = Sim900.from_config(prologix, '4')
  sim928   = Sim928.from_config(sim900, '1')

STYLE B (hybrid):
  prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
  sim900   = prologix.gpib(4)
  sim928   = sim900.slot(1)

STYLE C (explicit comm + params):
  prologix = PrologixGPIB.from_config(exp, '/dev/ttyUSB0')
  sim900   = Sim900.from_config(comm=prologix.gpib_comm('4'), params=prologix.child_params['4'])
  sim928   = Sim928.from_config(comm=sim900.slot_comm('1'), params=sim900.child_params['1'])

STYLE D (named-keyword connect):
  prologix = PrologixGPIB.connect(exp, port='/dev/ttyUSB0')
  sim900   = Sim900.connect(prologix, gpib='4')
  sim928   = Sim928.connect(sim900, slot='1')

STYLE E (full explicit init, no YAML at runtime):
  prologix = PrologixGPIB(port='/dev/ttyUSB0', baudrate=9600)
  sim900   = Sim900(prologix, gpib='4')
  sim928   = Sim928(sim900, slot='1', settling_time=0.4)
```

---

## Key design constraints (from discussion)

- **YAML is the single source of truth** for all instrument settings. Settings must not be
  baked into the generated Python file, since they would drift from the YAML and break the
  metadata record saved alongside experimental data.

- **Identity keys** (`'/dev/ttyUSB0'`, `'4'`, `'1'`) are acceptable in the Python file.
  These are structural/addressing identifiers, not settings. If they change, the physical
  wiring has changed and regenerating the file is the right response.

- **Pydantic `*Params` classes stay** as internal wizard infrastructure. They are used by
  `config_io` (YAML load/save), `params_discovery` (instrument scanning), and internally
  by `from_config` / `connect`. They do not need to appear in wizard-generated user files.

- **The parent/child hierarchy stays** in the YAML and in Params classes (for tree-walking
  and code generation). The live instrument objects do not need to maintain a `children`
  dict if all instruments are explicitly constructed in the generated file.

- **`from_params_with_dep`** becomes an internal implementation detail called by
  `from_config` / `connect`, rather than part of the public-facing API.

- **Hackability**: a user who wants to override a YAML value before connecting can do so
  by mutating the params object on the parent before the child is constructed, e.g.
  `parent.params.children['1'].settling_time = 0.8` followed by the normal connect call.
  This does not require any of the styles to expose Params classes directly.
