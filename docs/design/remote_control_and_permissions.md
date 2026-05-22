# Design: Remote Instrument Control & Permissions in the Wizard

**Status:** Draft for discussion (2026-05-21)
**Scope:** How the remote server + permission state machine (built in Phases 1–3)
should surface in the wizard UI and config layout. No implementation yet.

---

## Background

We have built, as a library:

- A **server** (`lab_wizard/lib/server/`) that hosts a local instrument tree and
  exposes every user-facing method over ZMQ + JSON-RPC.
- A **client** (`lab_wizard/lib/client/`) with `RemoteExp.from_attribute(name)`
  returning typed proxies, so a measurement runs against a local *or* remote
  tree unchanged.
- A **permission state machine** (`lab_wizard/lib/server/permissions.py`):
  instruments declare `_state_methods_`; the server records state after each
  call and blocks calls whose `when` condition is satisfied.

None of this is wired into the wizard GUI yet. This document decides how it
should be.

---

## Core principle: permissions are server-local

A permission rule references instrument state by path, and the gate only knows a
state value because a call **through that server** recorded it. A server has no
visibility into state on a different server. Therefore a rule can only
meaningfully reference instruments **hosted by the same server**.

This is not merely an implementation limit — it reflects physics. Safety
interlocks exist because instruments are electrically tied to one experiment
(bias line, pulse line, cryo amp in one rack). Those instruments are naturally
hosted by the one server next to that rack. **Interlocked instruments are
co-located by physics, so server-local rules are the correct scope.**

**Decision:** A server imposes rules only over the local instruments it
controls. Rules are authored against the local `config/instruments` tree.
Cross-server interlocks are out of scope (would require live state propagation
between servers).

---

## A workstation has three roles

The confusion about "remote instruments in manage_instruments" comes from one
page implicitly mixing roles. Separating them resolves it.

| Role | What it is | Config | UI |
|------|-----------|--------|----|
| **Host** | Instruments this machine drives + safety rules over them | `config/instruments/`, `config/server/server.yaml` | `manage_instruments` (exists) + **Manage Permissions** (new) |
| **Consume** | Remote instruments this machine's *measurements* use | `config/remote/servers.yaml` (new) | surfaced in measurement creation |
| **Run** | Build/run a measurement project | `projects/...` | `get_measurements` flow (exists) |

`manage_instruments` is already labelled "instrument configs for this
workstation" — the hosting role. It stays **local-only**.

---

## Config layout

```
config/
  instruments/              # LOCAL hardware this machine hosts (unchanged)
    dbay_key_<hash>.yml
    dbay_key_<hash>/<child>.yml
  server/
    server.yaml             # THIS machine's server: bind addr + permissions
  remote/                   # NEW — consuming side
    servers.yaml            # known remote servers: [{name, url}]
  savers/  plotters/  measurements/   # unchanged
```

- `config/server/server.yaml` `permissions:` block is authored by the **Manage
  Permissions** page, referencing local instruments.
- `config/remote/servers.yaml` is authored by a lightweight **Remote Servers**
  registration step; the wizard connects live (via `list_descriptions`) to
  enumerate each server's attributes during measurement setup. It does **not**
  feed the permission gate.

### Resolved: a server hosts the whole `config/instruments` tree

**Decision (2026-05-21):** the server hosts every instrument configured in
`config/instruments` — it is "this machine's instrument daemon." Phase 1's
per-project `exp_yaml` model is replaced (kept only as an optional override).

Concretely:

- On boot, the server loads the full `config/instruments` tree (the same data
  `get_configured_tree` / `load_instruments` already read for the wizard) and
  eagerly builds its `inst://` path index + `attribute_name` index.
- When asked what it can provide (`list_attributes` / `list_descriptions`), it
  returns **every local instrument that has an `attribute_name`**.
- When a client requests one by `attribute_name`, the server concretely
  initializes that instrument via the existing `Exp.from_attribute()` path
  (root `create_inst()` → `make_child()` chain → optional channel index) and
  caches the live object in the registry.

This means permissions are defined once per machine, against the same tree the
hosting UI shows. It also makes `attribute_name` the single contract between
hosting (what the server exposes), consuming (what clients request), and
permissions (what rules reference).

**Implementation note for the implementer:** the current
`lab_wizard/lib/server/registry.py` builds its index from an `Exp` object. To
host `config/instruments`, build an equivalent eager walk over
`load_instruments(config_dir)` (which returns the `{key: ParentParams}` dict),
or construct an `Exp`-like wrapper around it. Watch the hash-repair behavior
described below — load instruments through the same path that repairs stale
hashes so server and wizard agree on keys.

---

## UI plan

### 1. Manage Permissions (new page, linked from home)

- Shows the **local** instrument tree (`get_configured_tree`, same data as
  manage_instruments). No remote instruments.
- Rule builder produces entries for `server.yaml`'s `permissions.rules`:
  - **when**: pick instrument → pick a **state key** → operator
    (`equals`/`greater_than`/…) → value. Composable with all/any/not.
  - **deny**: pick instrument(s) → pick method(s).
  - message + id.
- Writes/reads the `permissions:` block; YAML remains the human-reviewable
  source of truth (safety config benefits from version control + review).

**`_state_methods_` drives the builder.** When the user picks an instrument for
a `when` condition, the UI offers exactly the state keys that instrument
declares, by introspecting `collect_state_methods(cls)` on the backend. The
mechanism we built for enforcement doubles as the UI's vocabulary — no separate
schema to maintain. Method lists for `deny` clauses come from class
introspection (public methods / behavior ABC).

### 2. Manage Instruments (unchanged)

Stays the local hardware tree. Do **not** add remote instruments here.

### 3. Remote instruments (consuming side)

- A small **Remote Servers** registration (page or section): add `{name, url}`,
  test connection, persist to `config/remote/servers.yaml`.
- During **measurement creation / instrument selection**: in addition to local
  matches (`discover_matching_instruments`), offer remote attributes from
  registered servers. The server reports each attribute's `behavior_abc`
  (`VSource`/`VSense`/…), which the wizard matches to the measurement's required
  resource types exactly like local instruments.
- Generated setup uses `RemoteExp.connect(url)` + `from_attribute(name,
  as_type=...)` (the `--remote` flow already in the setup templates).

---

## Schema refinement: author rules by `attribute_name`

Phase 3 rules use raw `inst://<hash>/...` paths. The hash in a path is **derived
from the params' key fields** (port, slot, gpib_address, ...) and is *volatile*:
editing a key field changes the hash.

### Where hashes get rewritten — `validate_and_repair_hashes`

- **Defined:** `lab_wizard/lib/utilities/config_io.py:451`.
- **Called from exactly one place:** `lab_wizard/lib/utilities/model_tree.py:226`,
  inside `load_exp_from_yaml(...)` — plus its own recursion into `children`
  (`config_io.py:480`).
- **What it does:** after a project YAML is parsed, it walks every node, recomputes
  the expected hash from the params, and if it differs from the stored key it
  rekeys the node **and rewrites the YAML in place** (`model_tree.py:228-232`).
- **When it runs:** on *every* `load_exp_from_yaml`. So if a user edits a `slot`
  or `port`, the next load silently changes that node's `inst://` path.

(Note: `load_instruments` for `config/instruments` is a separate path; when the
server is moved to host `config/instruments` it must run an equivalent repair so
the server's paths match the wizard's. Verify this during implementation.)

**Implication:** a rule that hard-codes `inst://<hash>/...` can be silently
broken by an unrelated key-field edit. By contrast, `attribute_name` is **stored
in the YAML and never derived** — it only changes if the user changes it. So it
is the stable handle, and is the correct thing for rules (and clients) to
reference.

**Recommendation:** extend `Condition`/`DenyClause` to accept an `attribute`
field as an alternative to `path`. The server resolves `attribute` → `inst://`
path at gate construction using its attribute→path index (it already builds one,
see `InstrumentRegistry._attribute_index`). Raw `path` stays supported for
advanced/manual use.

---

## Default attribute-name generation ("petnames")

`attribute_name` is the stable, human-facing handle for every exposed
instrument, so good defaults matter. Ideally a user sets something semantic
(`cryo_amp_bias_source`), but we should generate a usable default when they
haven't.

### The thing you're describing

Random-but-readable identifiers like `vanilla-seafoam-waxing` are usually called
**"petnames"** (Mark Miller's term) or **"codenames"** / **"friendly IDs."**
Common generators:

- **`coolname`** (Python) — produces exactly the adjective-adjective-noun slug
  style: `vanilla-seafoam-waxing`. Pure-Python, no deps, ~10k-word lists.
- **`haikunator`** — `delicate-rice-1234` (adjective-noun-token).
- Docker's `namesgenerator` — `vibrant_einstein` (adjective_surname); Heroku app
  names are the same idea.

### Recommendation

A bare petname is *memorable and stable* but *semantically empty* — it doesn't
tell you the instrument is a bias source. The current autogen
(`lab_wizard/wizard/backend/attribute_name_autogen.py`) already produces
type-based names (`dac4D`, `sim970`, `dac4D_ch1`) with `_2`/`_3` collision
suffixes; those hint at the type but the positional suffix can shift.

Prefer a **hybrid: `{type}-{petname}`**, e.g. `dac4d-vanilla-seafoam`:

- type prefix → tells you what it is at a glance,
- petname suffix → globally unique and stable (generated once, stored in YAML,
  never derived), so it survives slot/port edits — unlike the hash and unlike a
  positional `_2` suffix,
- still a strong nudge to rename to something semantic; the UI should surface
  the attribute name prominently and make renaming a one-click action.

This keeps the requirements satisfied: unique (1), stable (2), type-hinting (3),
memorable (4). Implementation lives in `attribute_name_autogen.py`; adding
`coolname` (or a tiny bundled word list) is the only new dependency.

---

## Non-goals / explicitly deferred

- **Cross-server interlocks.** A rule cannot reference an instrument on another
  server. If two interlocked instruments must be on different machines, that is
  a hardware-layout decision to revisit, not something the gate solves today.
- **Permission enforcement on the client.** The gate runs on the server; the
  client only surfaces `PermissionDeniedError`. No client-side duplication.
- **A full FSM (named modes).** We chose the rule-list + declarative-state model
  (see the Phase 3 plan); not revisiting here.

---

## Suggested build order

1. **Server hosts `config/instruments`** (decision resolved above). Change
   `server.py` / `registry.py` to build the index from `load_instruments`
   instead of a project `Exp`; ensure hash repair runs so paths match the
   wizard. Keep `exp_yaml` as an optional override.
2. **`attribute_name` rule references** + server-side resolution. Extend
   `Condition`/`DenyClause` with an `attribute` field; resolve via
   `InstrumentRegistry._attribute_index` at gate construction. Backend + schema
   only; no UI yet.
3. **Default attribute-name generation** — hybrid `{type}-{petname}` in
   `attribute_name_autogen.py` (add `coolname` or a bundled word list). Surface
   + one-click rename in the UI.
4. **Backend endpoints for the permissions UI**: list local instruments with
   their declared state keys + method lists; read/write the `permissions:`
   block. Reuse `get_configured_tree` + `collect_state_methods`.
5. **Manage Permissions page** (Svelte): tree view + rule builder.
6. **Remote Servers registration** + measurement-creation integration
   (consuming side). Independent of 1–5; can proceed in parallel.

Items 1–4 are the hosting/permissions track; item 5 is the consuming track.
They share no code and can be sequenced independently.
