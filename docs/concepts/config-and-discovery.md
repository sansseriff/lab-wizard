---
icon: lucide/folder-tree
---

# Config & discovery

This page covers three closely-related mechanisms:

- the **on-disk config tree** under `lab_wizard/config/`,
- how Lab Wizard **loads and saves** that tree
  ([`config_io.py`](../../lab_wizard/lib/utilities/config_io.py)),
- how instrument types are **auto-discovered** from source
  ([`params_discovery.py`](../../lab_wizard/lib/utilities/params_discovery.py))
  and how the GUI **probes hardware**
  ([`discovery.py`](../../lab_wizard/lib/instruments/general/discovery.py)).

## The config tree

```text
config/
├── instruments/                      # hierarchical: parents + nested children
│   ├── dbay_key_2da0863e.yml
│   ├── dbay_key_2da0863e/            # children folder, named after parent's file
│   │   └── dac4D_key_a0da5bfa.yml
│   ├── prologix_gpib_key_7bf897f7.yml
│   └── prologix_gpib_key_7bf897f7/
│       └── sim900_key_d6f1dcc9.yml
│       └── sim900_key_d6f1dcc9/
│           ├── sim928_key_c7fe1259.yml
│           └── sim970_key_8a3b1f04.yml
├── savers/                           # flat: one file per named instance
│   └── database_saver_key_main_db.yml
├── plotters/
│   └── mpl_plotter_key_iv_window.yml
├── server/
│   └── server.yaml                   # this machine's server bind + permissions
└── remote/
    └── servers.yaml                  # known remote servers (the address book)
```

Two storage shapes coexist:

- **Instruments are hierarchical.** Each node is a `<type>_key_<hash>.yml` file.
  A parent records its children as a mapping of `key → {type, ref}`, where `ref`
  points at the child's YAML file. Children live in a sibling folder named after
  the parent's file stem. This guarantees no key collisions even with multiple
  instances of the same type (two SIM900 racks each with a SIM928 in slot 1).
- **Savers and plotters are flat.** Each entry is a single
  `<type>_key_<name>.yml` file in `savers/` or `plotters/`, identified by a
  user-given name (no hashing, no hierarchy). Handled by
  [`flat_resource_io.py`](../../lab_wizard/lib/utilities/flat_resource_io.py).

## Hashing { #hashing }

A node's filename and in-memory dict key are an **8-char hex hash** computed from
its type plus its addressing value:

```python
instrument_hash("sim928", "1")   # -> e.g. "c7fe1259"  (type:key_value, sha256[:8])
```

The raw address (port, slot, GPIB address) is **stored as a normal field inside
the YAML** via the KeyLike mixin, and the hash is derived from it. This keeps raw
hardware addresses out of filenames and generated Python (which would be fragile)
while keeping them human-visible in the file content.

!!! warning "Hashes are volatile; `attribute_name` is stable"
    Because the hash is *derived* from the key fields, editing a `slot` or `port`
    changes the hash. [`validate_and_repair_hashes`](../../lab_wizard/lib/utilities/config_io.py)
    detects this on load and rewrites the file in place with the corrected key.
    For anything that needs a durable handle on an instrument — permission rules,
    remote references — use the **`attribute_name`** instead (see below), which is
    stored, never derived, and only changes when the user changes it.

## Loading and saving instruments

[`load_instruments(config_dir)`](../../lab_wizard/lib/utilities/config_io.py)
walks `instruments/*.yml`, parses each into its `Params` class (via
[discovery](#type-discovery)), recursively attaches children by following their
`ref`s, and returns a `dict[str, ParentParams]` keyed by hash. Disabled nodes
(`enabled: false`) are skipped. It also migrates legacy raw-address keys to
hashes on the fly.

`save_instruments_to_config(instruments, config_dir)` writes the tree back out,
preserving field-description comments (it uses `ruamel.yaml` round-trip mode).
`normalize_instruments` additionally deletes orphaned files and empty folders so
the on-disk tree exactly matches the in-memory tree.

The CRUD operations the GUI calls all live in `config_io.py`:
`get_configured_tree` (JSON for the frontend), `add_instrument_chain`,
`reinitialize_instrument`, `remove_instrument`, `initialize_instrument`.

### `add_instrument_chain`

Adding a deep instrument may require creating its parents too. The GUI sends a
leaf-first **chain**, each step tagged `create_new` or `use_existing`:

```json
[
  {"type": "sim928",       "key": "1",            "action": "create_new"},
  {"type": "sim900",       "key": "7",            "action": "create_new"},
  {"type": "prologix_gpib","key": "/dev/ttyUSB0", "action": "use_existing"}
]
```

`config_io` processes it root-first, reusing existing nodes and creating new ones,
then saves the whole tree.

## Type discovery (source scanning) { #type-discovery }

Lab Wizard never maintains a manual registry of instrument types. Instead,
[`params_discovery.py`](../../lab_wizard/lib/utilities/params_discovery.py)
**scans the source tree** for `Params` classes and builds a `type → module` map.

A class is registered if it:

- lives under `lib/instruments/` (or `lib/savers/`, `lib/plotters/`),
- inherits an allowed base (`CanInstantiate`/`ChildParams` for instruments,
  `SaverParams`/`PlotterParams` for the flat kinds), and
- declares a `type: Literal["..."]` discriminator field.

The scan is **regex-based on source text** (it does not import every module),
which is fast and avoids import side effects. Results are cached to
`~/.cache/lab_wizard/params_cache_<kind>.json`, invalidated by a folder
fingerprint (max mtime + file count). `load_params_class("dbay")` then lazily
imports and returns the class.

`get_metadata(kind)` produces the rich per-type metadata the GUI needs: defaults,
`key_hint`, parent chain, child types, and discovery-action specs.

!!! note "Three kinds, one mechanism"
    The same discovery machinery serves instruments, savers, and plotters via a
    `Kind` parameter. Instruments have a parent/child hierarchy; savers and
    plotters are flat. Adding a new saver type is literally "drop a
    `SaverParams` subclass with a `type` Literal into `lib/savers/`."

## Hardware discovery (probing) { #hardware-discovery }

Distinct from *type* discovery, **hardware discovery** is the GUI's "scan for what's
actually connected" feature. An instrument's `Params` class inherits
[`Discoverable`](../../lab_wizard/lib/instruments/general/discovery.py) and
returns a list of `DiscoveryAction`s. Each action declares:

- a Pydantic `params_model` (the GUI renders a form from its fields), and
- a handler whose **return annotation** names the result shape.

Results are a closed discriminated union the frontend branches on:

| Result | Meaning | Outcome |
|---|---|---|
| `ProbeResult` | found connection candidates (e.g. serial ports) | user picks one; its value becomes the key |
| `SelfCandidatesResult` | instances of *this* instrument found on a bus | user picks one |
| `ChildrenResult` | sub-instruments found under a parent | applied automatically |

For example, `DBayParams._discover_children` hits the DBay server's
`/full-state` HTTP endpoint and returns a `ChildrenResult` listing the DAC
modules and their slots. The backend endpoint `/api/manage-instruments/discover`
runs the action (walking and initializing the live parent chain first if the
action declares a `parent_dep`), and the GUI applies the result.
