---
icon: lucide/settings-2
---

# Managing instruments

**Manage Instruments** (`/manage_instruments`) is the editor for the local
hardware this workstation drives — the **host** role. It reads and writes
`config/instruments/`. It is intentionally **local-only**: remote instruments
never appear here (you register those under [Remote Servers](../remote/operations.md)).

## What the page shows

`GET /api/manage-instruments` returns:

- **`tree`** — the configured instrument tree (`get_configured_tree`), a list of
  top-level instruments each with nested `children`, each node carrying its
  `type`, `key` (hash), and `fields`.
- **`metadata`** — for every discoverable instrument type
  ([`get_instrument_metadata`](../concepts/config-and-discovery.md#type-discovery)):
  defaults, `key_hint`, parent chain, child types, and discovery-action specs.

The metadata drives the "add instrument" UI: it knows which types are top-level
vs children, what each type's parent chain is, and what default field values to
seed a new node with.

## Adding an instrument

Because a child may need parents that don't exist yet, the GUI builds a
leaf-first **chain** and posts it to `POST /api/manage-instruments/add`
(→ [`add_instrument_chain`](../concepts/config-and-discovery.md)). Each step is
either:

- `use_existing` — reuse a node already in the config, or
- `create_new` — create it from defaults (plus any `extra` field overrides).

The chain is processed root-first; the raw key value you supply (port, slot,
GPIB address) is written into the params and the node is stored under its
[derived hash](../concepts/config-and-discovery.md#hashing).

The `key_hint` on each type tells you what the key means, e.g. *"USB port (e.g.
/dev/ttyUSB0)"*, *"Slot number (e.g. 1)"*, *"GPIB address (e.g. 4)"*.

## Discovering hardware

Instead of typing addresses, you can **probe** for connected hardware if the
instrument's `Params` class is `Discoverable`. `POST /api/manage-instruments/discover`
runs the chosen [discovery action](../concepts/config-and-discovery.md#hardware-discovery).
Depending on the result type the GUI will:

- list serial-port candidates for you to pick (`ProbeResult`),
- list instances found on a bus for you to pick (`SelfCandidatesResult`), or
- auto-apply discovered sub-modules (`ChildrenResult`), via
  `POST /api/manage-instruments/apply-children`.

If a child's discovery action needs a live parent (e.g. scan a GPIB bus through
the Prologix controller), the backend walks and initializes the resolved parent
chain first, then disconnects it when done.

## Editing, resetting, removing

| Action | Endpoint | Effect |
|---|---|---|
| Reset | `POST /api/manage-instruments/reset` | `reinitialize_instrument` — restore default field values, **preserve children** and the key field (so the hash is stable) |
| Remove | `POST /api/manage-instruments/remove` | `remove_instrument` — delete the node and clean up orphaned files/empty folders |

## `attribute_name` — the stable handle

Leaf instruments (and individual channels) can carry an `attribute_name` — a
human-given identifier like `cryo_amp_bias_source`. This is the durable handle
used by:

- **remote `from_attribute`** project generation (the generated setup asks the
  server for the instrument by this name),
- the **permission gate** (rules reference instruments by `attribute_name`), and
- the **instrument server** (it exposes every named instrument).

Unlike the hash key, `attribute_name` is stored and never derived, so it survives
edits to slot/port. Set semantically meaningful names for anything you intend to
reference remotely or in a safety rule.
