# Server / client architecture plan

> **Status: mostly complete; Phase 9 proposed.** Phases 0-8 are built and
> tested, with two corrections recorded in Phase 9 (2.5's concurrency does not
> reach the socket path; 7.2's leases are never acquired by projects). Phase 9 —
> run-scoped claims — is designed but not built. Also remaining, tracked in
> [Remaining](#remaining):
> push notifications (6.3), forced `attribute` references in the rule builder
> (8.6), per-attribute source selection in `select_instruments`, and the
> read-only / edit-destination UI for a merged multi-source tree.
>
> The per-attribute source selection gap is live: `CompositeResources` routes
> correctly and the generator emits `instrument_sources`, but
> `list_instrument_sources` deliberately withholds this workspace's *own*
> daemon from the picker, so the conflict warning recommends a fix the UI
> cannot perform. See [procedure_plan.md](procedure_plan.md) for the adjacent
> params work.

How Lab Wizard should behave when a server and one or more clients run on the
same machine, and what has to be built to get there.

The motivating problem: a VISA/serial handle cannot be opened by two processes,
but you still want several programs sending commands to that instrument. The
server exists to solve exactly this — but today the wizard GUI *also* opens
hardware in-process, and there is no arbitration between them.

---

## 1. Current state

### Three processes, one config tree

| Piece | Owns | Notes |
|---|---|---|
| Wizard ([main.py](../lab_wizard/wizard/backend/main.py)) | writes `config/instruments/*.yml` | FastAPI + webview; **also opens hardware in-process** |
| Server ([server.py](../lab_wizard/lib/server/server.py)) | live instrument handles | reads config at boot, ZMQ ROUTER + JSON-RPC |
| Client ([remote_resources.py](../lab_wizard/lib/client/remote_resources.py)) | nothing | typed proxies, no local config by design |

Wire surface is six methods ([wire.py:63-68](../lab_wizard/lib/server/wire.py#L63-L68)):
`call`, `list_paths`, `list_attributes`, `describe_path`, `describe_attribute`,
`list_descriptions`. Everything is read-only except `call`.

### Known defects this plan addresses

1. **Two hardware owners.** [main.py:544](../lab_wizard/wizard/backend/main.py#L544)
   calls `root_params.create_inst()` directly for discovery. Nothing checks
   `server_status()`. Start the server, have a client touch an instrument, then
   hit "Discover" → resource-busy.
2. **The gate can be defeated by the GUI.** `StateTracker` only records calls
   that pass through the server. In-process wizard access silently corrupts the
   safety model.
3. **Nothing is ever disconnected.** The registry caches live objects from first
   `resolve` until process exit; there is no teardown anywhere.
4. **Remote use is all-or-nothing.** `--remote <url>` replaces the entire
   resource source. A project is fully local or fully remote — there is no
   per-instrument mixing.
5. **No flow for administering a server.** `config/remote/servers.yaml` is
   consumption-only. You cannot add, remove, or configure instruments on a
   server from a client.
6. **Proxies cache a frozen path.** [remote_resources.py:82-88](../lab_wizard/lib/client/remote_resources.py#L82-L88)
   caches `name → proxy` with the `inst://` path baked in. Latent bug the moment
   the tree becomes mutable.

### What is *not* broken

The DBay integration is current. [lab_wizard/lib/instruments/dbay/dbay.py](../lab_wizard/lib/instruments/dbay/dbay.py)
is on the unified `DBayClient` API and every call site is valid. (The stale
pre-lab-link snapshot under `ref/dbay-backend/` was deleted in Phase 0.2 — it
misled readers about which transport DBay actually uses.)

---

## 2. Core concepts

### 2.1 Declared vs held

The registry is lazy: `_factories` holds everything it *could* serve, `_index`
holds only what it has actually instantiated
([registry.py:284-292](../lab_wizard/lib/server/registry.py#L284-L292)).

**The server declares everything and holds almost nothing.** Listing is not
owning. Every arbitration question below keys off `_index`, never off config.

### 2.2 Two orthogonal declarations

These look correlated but are not — an instrument with a front panel is
exclusive *and* externally mutated.

```python
def transport_sharing(self) -> Literal["exclusive", "shared"]:   # default "exclusive"
def state_authority(self) -> Literal["inferred", "subscribed"]:  # default "inferred"
```

| Root | sharing | authority | why |
|---|---|---|---|
| Prologix / SIM900 / any serial | exclusive | inferred | `++addr N` is global mutable controller state; replies carry no address |
| DBay, `mode="gui"` | **shared** | **subscribed** | GUI backend is a `LabSync` reactive server that broadcasts to all clients |
| DBay, direct serial | exclusive | inferred | one serial handle |
| DBay, direct UDP | exclusive | inferred | no connection to own, but reply attribution races across processes |

Default to `exclusive` / `inferred` — a new instrument is conservative until
someone thinks about it.

### 2.3 Ownership unit

Arbitrate at **transport granularity**, not instrument granularity. Channels
under one root share one serial/HTTP connection, so the claim key is the
transport identifier (VISA address / serial device / host:port), falling back to
the root key. This keeps the arbitration surface to a handful of racks rather
than a lattice of partial conflicts — and catches the
same-device-configured-twice mistake for free.

### 2.4 Declare, don't infer

No recency heuristics ("what the server used lately"). Non-reproducible,
untestable, unexplainable in an error message. Explicit claim, explicit release,
pid liveness for staleness — reusing the pattern already proven in
`server_status` ([server_control.py:204-211](../lab_wizard/wizard/backend/server_control.py#L204-L211)).

### 2.5 Cooperative, not airtight

Anyone can open pyvisa in a REPL. Real OS exclusion exists for serial ttys and
not at all for HTTP-backed instruments. The goal is catching honest mistakes
with a legible error, not defending against an adversary. Say so in the docs.

---

## 3. Target topology

**The server is the workstation's hardware daemon. The wizard is always a client
of it. There is no "local" special case for hardware access.**

- Server dual-binds one ROUTER: `ipc:///tmp/lab_wizard-<workspace-hash>.sock`
  for same-machine, plus optional `tcp://` for remote.
- Wizard does find-or-start on its workspace server at launch.
- `ipc://` vs `tcp://` is the natural authority boundary: same-machine peers
  already have filesystem access to the YAML, so granting them full admin loses
  nothing. Remote peers get read + `inst.call` by default.
- A no-server mode still exists for scripts and tests (the eager
  `InstrumentRegistry(resources)` path), but the GUI is never a second hardware
  owner.

---

## Phase 0 — Cleanups ✅ done

No design risk. Do first.

| # | Item | Where |
|---|---|---|
| 0.1 | Bump `dbay>=0.6.0`, refresh `uv.lock`. 0.4.1 fixed `ADC4D.CORE_TYPE` casing; 0.5.0 added the state subscriptions and 0.6.0 the shared state schema that **Phase 3 depends on** | `lab_wizard/pyproject.toml` |
| 0.2 | Delete `ref/dbay-backend/` — stale pre-lab-link snapshot with `http.py` | `ref/` |
| 0.3 | Pass `exclusive=True` to `pyserial.Serial(...)` — converts silent byte-theft into an immediate error | [serial.py:67](../lab_wizard/lib/instruments/general/serial.py#L67) |

---

## Phase 1 — Taxonomy + cheap safety net ✅ done

Highest value per line in the plan. No governance decisions.

- **1.1** ✅ `transport_sharing()` / `state_authority()` / `transport_key()` on
  `CanInstantiate` — the roots are exactly the things that open a transport.
  Types and conservative defaults in
  [`instruments/general/transport.py`](../lab_wizard/lib/instruments/general/transport.py).
  Implemented for Prologix (serial), DBay (mode-dependent), Keysight 53220A and
  Yokogawa AQ2212 (single-session sockets). SIM900 needed nothing — it is a
  *child* of the Prologix bus and inherits its root's transport.
- **1.2** ✅ `registry.transport_for()` resolves any path to its root's
  declarations, and `describe_path()` carries them. Roots that duck-type
  `create_inst` without inheriting `CanInstantiate` fall back to the safe
  default rather than failing to register.
- **1.3** ✅ `registry.list_held()` / `held_roots()` / `exclusive_roots()`, and
  a `list_held` RPC returning all three. Makes declared-vs-held visible.
- **1.4** ✅ [`client/preflight.py`](../lab_wizard/lib/client/preflight.py), wired
  into both setup templates.
  [`client/server_discovery.py`](../lab_wizard/lib/client/server_discovery.py)
  finds the workspace's bind by reading `server.yaml` — a file read, never a
  port scan. An unreachable server is not an error: local projects must still
  run when none is up.
- **1.5** ✅ [`wizard/backend/transport_status.py`](../lab_wizard/wizard/backend/transport_status.py)
  behind `GET /api/transport-status` and `POST /api/transport-status/check`.
  Reports `held_conflicts` (local run fails now) separately from
  `configured_conflicts` (works now, breaks the moment the server touches that
  rack), and flags `duplicate_transports` where two roots resolve to one
  device.

Covered by [`tests/test_transport_sharing.py`](../tests/test_transport_sharing.py)
(18 tests) plus a live-server check that a held exclusive root refuses a local
project while a held *shared* root does not.

**Not yet wired into the UI.** The endpoints exist and are tested; rendering the
badges and the conflict warning is Phase 5.

---

## Phase 2 — One hardware owner ✅ done

- **2.1** ✅ `WireServer` takes a list of binds and binds one ROUTER to all of
  them. The server adds `workspace_ipc_endpoint(config_dir)` alongside the
  configured `tcp://`, so same-machine clients need no discovery. Disable with
  `server.ipc: false`. ipc socket files are removed on shutdown — a stale one
  would make a dead server look live to anything probing by existence.
- **2.2** ✅ `ensure_server()` in `server_control.py`, called from the wizard's
  lifespan. Quiet on failure: no `server.yaml` means the workstation never
  opted in, and a taken port usually means another wizard — neither should stop
  the GUI launching, since the in-process fallback still works.
- **2.3** ✅ `discover` RPC on the server, and
  [`wizard/backend/hardware_access.py`](../lab_wizard/wizard/backend/hardware_access.py)
  routing the wizard's discovery through it when one answers. The in-process
  path remains only as the no-server fallback. Server-side discovery resolves
  the parent chain **through the registry**, so it reuses the already-open
  connection instead of building a second one, and no longer disconnects the
  parent afterwards — the registry owns that lifetime now.
  `GET /api/hardware-owner` reports which process owns the hardware.
- **2.4** ✅ `registry.release(path)` / `release_all()`, deepest-first so
  children let go before the root whose transport they borrow. `_teardown()`
  tolerates every convention in the codebase (`disconnect()`, `close()`,
  `dep`/`_dep`/`client`.`close()`) and never raises, so one uncooperative
  instrument cannot strand the rest. Wired into `serve_forever`'s shutdown, and
  exposed as a `release` RPC so an operator can hand back one rack without
  stopping the server. Release is *eviction*, not removal: the factory stays,
  and the next call reopens.
- **2.5** ✅ `registry.transport_lock(path)` — one `RLock` per root, held across
  the whole transaction in `WireServer.call` (resolve + dispatch + state
  record). Covered by a concurrency test asserting that two calls on one root
  never overlap while calls on different roots do.

Covered by [`tests/test_single_hardware_owner.py`](../tests/test_single_hardware_owner.py)
(11 tests) plus a live check: server on ipc, wizard discovering it, `release`
RPC closing the handle, shutdown releasing the rest and cleaning the socket,
and the wizard correctly falling back once the server is gone.

**Known gap:** `apply-children` still writes config directly in the wizard —
that is a config write, not a hardware operation, so it belongs to the `tree.*`
work in Phase 8 rather than here.

---

## Phase 3 — DBay live state ✅ done

DBay's GUI backend is a `LabSync` reactive server, so anyone with the GUI open
can move a channel. A gate that only recorded its own writes was confidently
wrong about live hardware.

- **3.1** ✅ shipped in dbay 0.5.0 (`on_patch`, `on_snapshot`, `state_version`).
- **3.2** ✅ [`server/external_state.py`](../lab_wizard/lib/server/external_state.py).
  `attach_external_state()` subscribes every root declaring
  `state_authority() == "subscribed"`; `DBayParams.state_authority_client()`
  builds a read-only client (`load_state=False`) distinct from the command one.
- **3.3** ✅ [`instruments/dbay/state_sync.py`](../lab_wizard/lib/instruments/dbay/state_sync.py)
  — a declarative table, not patch parsing. `bias_voltage → voltage`,
  `activated → output` (bool to the gate's `"on"`/`"off"`), plus dac16D's
  singleton `vsb`/`vr` addons. Pure functions over a snapshot, so it is testable
  without hardware or a socket.

  Worth noting the subscribed path is *better informed* than inference, not
  merely fresher: `Dac4DChannel` has no separate output enable and so never
  records `output` at all, while the authority tracks it.
- **3.4** ✅ Echoes dropped via `origin_client_id`; a `state_version` gap forces
  a full resync rather than letting patch-derived state drift.
- **3.5** ✅ Seeded from `snapshot()` at connect. Resync **clears before
  seeding**, so anything absent from the new snapshot reads as unknown rather
  than as a stale claim about live hardware — the same reasoning behind DBay's
  own `_reset_transient_flags`.
- **3.6** ✅ `StateTracker.record()` is a no-op for subscribed paths. The
  concrete failure it prevents: we command 10 V, the hardware clamps to 5, and
  recording our own value would leave the gate believing 10.
- **3.7** ✅ `StateTracker` now holds an `RLock` — writes arrive from both the
  request loop and the subscription callback thread.

Subscription failure is non-fatal but logged loudly: a gate running on inferred
state for a subscribed root is exactly the silent wrongness this removes.

Covered by [`tests/test_external_state.py`](../tests/test_external_state.py) (15 tests).

## Phase 4 — Tree visibility, multi-source, mixed projects ✅ done

- **4.1** ✅ `tree_get` RPC — same shape `/api/manage-instruments` returns, plus
  per-root transport facts and `held_roots`. Refuses in single-project hosting
  mode, which has no editable tree.
- **4.2** ✅ `schema_get` RPC — instrument metadata and permission vocabulary
  come from the **server's** build, which can differ from the client's. Server
  sends data and schema; the client renders.
- **4.3** ✅ `list_attributes` left narrow, as the measurement-binding contract.
- **4.4** ✅ [`client/server_registry.py`](../lab_wizard/lib/client/server_registry.py).
  Servers advertise to `~/.lab_wizard/servers/<hash>.json` on start and withdraw
  on exit; discovery is a directory read, never a port scan. Dead entries are
  reaped by pid liveness.

  **This closed a real hole.** Workspace-scoped lookup is structurally blind to
  a server started elsewhere on the box — its ipc path is hashed from *its*
  config dir and its `server.yaml` is somewhere we have no reason to look. So
  preflight silently approved projects that could not run. `preflight_local_project`
  and `transport_status` now consult every server on the machine, and
  cross-workspace overlap is matched on **`transport_key`**, because the same
  device under two configs has two different root hashes.
- **4.5** ✅ [`client/composite_resources.py`](../lab_wizard/lib/client/composite_resources.py)
  routes `from_attribute` per attribute across a local tree and any number of
  servers — possible only because `ResourceConfig` and `RemoteResources` already
  share that interface. Ownership lives in the project YAML
  (`resources.instrument_sources`), so a project runs identically for everyone
  instead of depending on a remembered flag. `--remote` survives as an explicit
  override; an absent mapping means local, so existing projects are untouched.
- **4.6** ✅ `config_status` RPC compares the registered path set against a fresh
  load, so a reformat or comment edit correctly reports no change while a real
  edit reports `diverged` with added/removed paths. A config that no longer
  loads is itself a reportable answer.

Covered by [`tests/test_server_registry.py`](../tests/test_server_registry.py)
(18 tests) plus a live run of a real server process: discovered from outside its
workspace, serving `tree_get` / `schema_get` / `config_status`, and withdrawing
on exit.

**Not yet wired into the UI** — the endpoints exist and are tested; rendering is
Phase 5.

## Phase 5 — UI ✅ partly done

The backend of phases 1–4 was invisible; this makes it pressable.

- **5.1** ✅ New **Hardware & Servers** page (`/hardware_status`), linked from the
  home page under "This workstation". Shows which process owns hardware, every
  server on the machine with its workspace and pid, and per-root transport
  status. Provenance also appears on Manage Instruments, which now says whether
  a Discover will run on the server or in the wizard.
- **5.2** ✅ `TreeNode` gained an optional `transportBadge` prop — optional so
  every existing caller renders unchanged — showing `exclusive`/`shared`,
  `subscribed`, and `in use`. Only depth 0 is badged, since only roots own a
  transport. Wired into Manage Instruments.
- **5.5** ✅ Live conflict warnings while choosing instruments, separating
  **held** (a local run refuses to start now) from **configured** (works now,
  breaks the moment anything uses that rack through the server). The check
  re-runs on each selection change and discards stale replies.
- **5.6** ✅ Hardware owner is stated rather than implied, with a pointer to
  create the server config when none exists.
- **5.7** ✅ Refresh on the status page; a **Release** button hands one rack back
  without stopping the server.

**Deliberately not built yet:** 5.3 (read-only mode) and 5.4 (explicit edit
destination for a merged multi-source tree). Both describe UI for editing a
*remote* server's tree, which is Phase 8 — writing it now would be speculative.
The merged-tree view therefore still shows only this workspace's tree; other
machines' servers appear on the status page rather than as tree roots.

Also still an info box, not selectable options: remote attributes in
`select_instruments`. The `CompositeResources` plumbing (4.5) exists, but the
generator does not yet emit `instrument_sources`, so per-attribute selection has
nothing to write to. That is the natural next increment.

## Phase 6 — Client robustness ✅ mostly done

- **6.1** ✅ Proxies carry their `attribute_name` and re-resolve on a
  "no instrument registered at path" error. `inst://` paths are hash-derived, so
  a key-field edit moves an instrument and every proxy holding the old path
  pointed at nothing forever — a latent bug that mutable trees would have
  exposed. Retry is deliberately narrow: driver faults, permission denials and
  timeouts propagate untouched.
- **6.2** ✅ `Session` rebuilds its socket once on timeout. A DEALER queues
  sends against a dead peer and then waits out the full timeout, so a server
  restart left the socket permanently useless with nothing to distinguish it
  from a slow instrument.
- **6.3** ⬜ Server→client push. Still polling. The event log added in Phase 8
  gives it something concrete to push, and lab-link remains the reference for
  the semantics (versioned snapshot + patch, `origin_client_id`).

---

## Phase 7 — Leases ✅ done

- **7.1** ✅ [`client/leases.py`](../lab_wizard/lib/client/leases.py). Claims keyed
  on the **transport identifier**, not the config path, so two workspaces naming
  one serial port contend despite different root hashes. Acquisition is atomic
  (`O_EXCL`), so two processes racing cannot both believe they won.
- **7.2** ✅ Filesystem-based, so it works with or without a server running —
  which also fixes two *local* projects colliding, previously unmitigated.
- **7.3** ✅ pid liveness, with a real bug found and fixed: `os.kill(pid, 0)`
  raises `PermissionError` for a process owned by another user, and the old
  `except OSError` treated that as dead. A live holder's claim would have been
  reaped and its hardware handed to a second process. Only `ProcessLookupError`
  now counts as gone — fixed in `leases`, `server_registry` **and the
  pre-existing `server_control`**, which had the same bug.
- **7.4** ✅ The server declines to open a claimed rack, but keeps serving one it
  already holds (a claim taken later does not evict it) and ignores claims on
  shared transports.

---

## Phase 8 — Tree writes + governance ✅ done

- **8.1** ✅ `tree_add` / `tree_remove` / `tree_reset` RPCs writing through
  `save_instruments_to_config`, so the YAML audit trail survives. Each **refuses
  while the rack is held** — see below.
- **8.2** ✅ Peer identity and arrival transport captured per request via a
  `ContextVar`. **This required correcting Phase 2.1:** one ROUTER bound to both
  endpoints was elegant but ZMQ does not report which endpoint a message arrived
  on, making the authority rule unimplementable. Now one socket per transport,
  so "arrived locally" is a property of the receive path.
- **8.3** ✅ `ipc://` peers may edit; `tcp://` peers get read + `inst.call`.
  Reaching the ipc socket needs filesystem access to it, so same-machine is
  proven by the OS rather than claimed in a payload. A same-machine client that
  chooses tcp gets the smaller set — privilege is opted into, never ambient.
- **8.4** ✅ Rule editing stays local-only.
- **8.5** ✅ Removal shows which rules reference an instrument before confirming,
  since a rule left pointing at a vanished attribute fails closed and can block
  instruments the user did not remove.
- **8.6** ⬜ `attribute` vs raw `path` in the rule builder — not forced yet.

### Why a held rack cannot be reconfigured

Rebuilding the index under a live instrument fails three ways at once: the old
object keeps the serial handle with nothing able to reach it; the next call
tries to open a port that is still held; and an in-flight call holds a lock from
the old lock table while new calls take one from the new table — two locks for
one bus, which is the Prologix desync. Requiring the rack to be free removes all
three, and `registry.adopt_live()` carries *unaffected* racks across the reload
so an edit in one place does not disturb another.

### Audit log

[`server/events.py`](../lab_wizard/lib/server/events.py) records who changed what,
as JSONL beside the config plus a bounded in-memory tail. Once another workspace
can edit your config, "who added this" stops being answerable from git alone.
Surfaced in the remote-tree page as a "Recent activity" panel — deliberately
minimal, enough to grow into a real feature if it earns one.

Covered by [`tests/test_authority_and_leases.py`](../tests/test_authority_and_leases.py)
(21 tests) plus a live check: tcp refused an edit, ipc performed one, a held rack
refused reconfiguration, and the event recorded the actor.

---

## Phase 9 — Ownership: run-scoped claims ⬜

Everything up to Phase 8 decides *which process* owns hardware. Nothing decides
*which measurement* may drive an instrument once several clients share one
server — and sharing is the reason the server exists. Two routed projects on one
counter can interleave `set_threshold(30)`, `set_threshold(50)`, `count()`, and
one client's safe-state exit can switch off a source another is mid-sweep on.

### The rule

**Writes need a claim. Reads need nothing. Claims by different runs must be
disjoint.** A running measurement claims the instruments it drives for the
duration of the run; a write from anyone else to a claimed path gets a legible
refusal naming the holder.

1. A claim on a path covers that path **and everything under it**.
2. Claims held by different tokens must not overlap: nobody may claim a path
   while another token holds that path, an ancestor of it, or a descendant.
   Siblings coexist.
3. A write to a path requires the caller's token to hold a claim covering it.
4. Reads — declared pure queries — are always allowed.

This is a borrow checker over the instrument tree. Two runs holding channel 1
and channel 2 is `&mut s.a` alongside `&mut s.b`: disjoint fields, both fine.
A run holding the whole counter is `&mut s`, and excludes both. The one
deliberate difference from Rust is rule 4 — Rust forbids `&` while a `&mut`
exists — because watching a running measurement is valuable. That is only safe
if "read" is defined strictly; see 9.2.

**Claims are per run, not per role.** One procedure that counts a calibration
detector on channel 1, changes an attenuation, then counts a test detector on
channel 2 holds one token covering both channels — or the whole counter — and
none of these rules ever apply between its own steps.

What the rules enable is **interleaving between runs**: run A counting on
channel 1 and run B counting on channel 2 of the same 53220A, alternating on a
gate-time timescale. That is sound only if two further properties hold, which
9.1 and 9.12 make explicit:

- **Transactional operations.** An operation on a channel re-establishes, in one
  call and therefore under one `transport_lock` hold, every piece of shared
  state it depends on. The 53220A's `count()` already does: `read_counts` arms
  and reads inside a single method, and `arm` re-applies function, trigger,
  gate, and input settings whenever the cached setup — keyed on channel — does
  not match.
- **Unclaimed state is baseline state.** Shared state (the 53220A's trigger and
  gate) can only be changed under a claim on its owner — the root — which by
  rule 2 excludes every other run. When that claim is released or expires, the
  server restores baseline on the subtree it covered. So a run holding only a
  channel can always rely on its ancestors being at their configured baseline.

Ownership is **orthogonal to the permission gate**, the same way sharing and
authority are orthogonal in 2.2. The gate answers *is this call safe given the
hardware's state*; a claim answers *may this caller drive this path now*. A call
must pass both.

### Corrections this depends on

- **Phase 2.5 built the lock, not the concurrency.** `transport_lock` exists and
  its test proves overlap across roots — but the test calls `WireServer.call`
  from threads directly. The socket path is still one `poller.poll()` loop that
  handles each request inline, so over the wire a `count(1000)` blocks every
  other client, including a claim renewal. `docs/roadmap.md`'s "single-threaded
  poll loop" item was right.
- **Phase 7.2 is not wired into projects.** Leases exist and are tested, but
  generated setup files only call `preflight_local_project`, which checks and
  refuses. Nothing ever *acquires* a lease, so two locally-run projects on one
  rack still collide.

### Items

- **9.0** **Threaded dispatch.** Hand each request to a worker pool after
  receipt, reply through a single sender, and keep `transport_lock` as the
  per-root serialisation it was designed to be. Prerequisite for 9.6: claim
  expiry cannot be implemented on a loop that one long call can stall.
- **9.1** **Claimable granularity is declared, and defaults to the root.** A
  driver declares `channels_claimable()` (and, for mainframes, whether slot
  modules are) — beside `transport_key()` — meaning *my channel-level
  operations are transactional, and all shared state is owned by an ancestor*.
  Undeclared means **false**: `registry.claim_unit_for(path)` then resolves any
  path to the root, so claiming one channel claims the whole instrument. That
  is the conservative answer for a driver nobody has reasoned about, like
  `transport_sharing`'s default.

  *Worked example — the 53220A declares its channels claimable.* Its manual
  (`literature/9018-03351.pdf`) shows one measurement engine: `CONFigure`
  selects function and channel together, `TRIGger:*` is a single system
  trigger, and gate commands are scoped by function, never by channel. Only
  `INPut[{1|2}]` is per input. So it can never *simultaneously* count on both
  inputs — but it does not need to. Interleaved counts are correct because
  `count()` is transactional (see The rule), and trigger and gate live on the
  root, where changing them needs a root claim. One consequence falls out
  correctly: gating one input on the other (`gate_source = "input2"`) is root
  state, so only a run holding the whole counter can use it — right, since that
  measurement occupies both inputs.

  A two-channel function generator is the easy case: each output has its own
  `SOURce1` / `SOURce2` subsystem, so channels are claimable with no shared
  state to reason about. (No function-generator driver exists in the repo yet.)

  Mainframes follow the same shape: SIM900 slot modules share the Prologix
  transport but not state, and every call is `++addr` plus command under one
  lock hold, so slots are claimable. DBay channels likewise.
- **9.2** **Query allowlist, failing closed.** Each class declares its pure
  queries — `_query_methods_ = {"get_voltage", "get_threshold", ...}` — merged
  across the MRO like `_state_methods_`, with behavior ABCs declaring their
  getters once. **Anything undeclared is a write.** `_state_methods_` cannot be
  reused for this: it is opt-in tracking of *safety-relevant* state, so most
  mutating methods are absent from it, and absence would silently read as
  "harmless". Note `Counter.count()` is a write — on the 53220A it issues
  `CONFigure` and arms the box.
- **9.3** **`ClaimTable`** on the server:
  `{unit_path: Claim(token, holder_label, peer, acquired_at, expires_at)}`
  under its own lock, never under a transport lock. In memory: a server restart
  drops all claims, and the holder's next write fails with "unknown claim",
  which aborts the run legibly rather than letting it continue unowned.
- **9.4** **RPCs**, available to `ipc` **and** `tcp` peers — a measurement on
  another machine must be able to claim:
  - `claim_acquire(paths, holder_label, ttl_s) -> {token, units, expires_at}` —
    **all-or-nothing**. A run needing two instruments can never end up holding
    one while waiting on the other, so there is no acquisition order to get
    wrong and no deadlock.
  - `claim_renew(token)`, `claim_release(token)`, `claim_list()`.
  - The token is **server-issued**, not client-chosen: it costs nothing and
    keeps a guessed token from impersonating a holder, even in a cooperative
    model.
- **9.5** **Enforcement in `WireServer.call`**, in this order: resolve the path
  → if the method is not a declared query, require that the request's token
  holds a claim covering `claim_unit_for(path)` → permission gate → dispatch.
  A new `ClaimDenied` names the holder label and expiry. **Unclaimed paths stay
  open** to token-less writes, so wizard discovery and ad-hoc scripts keep
  working; once any claim covers a path, only its holder writes. Acquisition
  checks rule 2 atomically against the whole table.
- **9.6** **Expiry and renewal.** TTL of the order of 30 s, renewed by the
  client in the background. A crashed client's claim lapses on its own. pid
  liveness is useless across machines, and a `Session` rebuilds its socket on
  timeout (6.2), changing its ZMQ identity — which is why the claim is keyed
  on the token and never on the connection. Expiry does exactly what release
  does, including 9.12's baseline restore.
- **9.7** **Client side:** `Session` attaches the active token to every `call`;
  a `RunClaim` context manager in `lib/client/claims.py` acquires, renews on a
  thread, and releases in `finally`. The runtime ordering it slots into is in
  `procedure_plan.md` Phase 7.
- **9.8** **Existing refusals learn about claims.** `tree_add` / `tree_remove`
  / `tree_reset` and `release` already refuse while a rack is *held*; they
  should also refuse while any unit under it is *claimed*.
- **9.9** **Audit.** Acquire, renew-failure, release, expiry, and denial are
  recorded in `server/events.py`.
- **9.10** **UI.** Hardware ownership page lists live claims — holder, units,
  expiry — with a force-release for same-machine callers only, and the conflict
  warning on `/measurements/resources` shows a claimed instrument as busy.
- **9.11** **Wire leases into local runs.** Generated setup files acquire the
  transport lease for each local root instead of only preflighting (fixes the
  7.2 gap above). The local analogue of a claim, at transport granularity,
  which is correct because a local run owns the whole transport.
- **9.12** **Release restores baseline.** On release or expiry the server calls
  `apply_baseline()` on the released subtree and clears driver caches that
  describe hardware (the 53220A's `_forget_hardware_state()`), keeping
  "unclaimed state is baseline state" true. Baseline covers category-2 params
  only; outputs are already in their safe state from the run lifecycle, and the
  two sets are disjoint.
- **9.13** **Interleaving acceptance test.** Two runs, one token each, holding
  channels 1 and 2 of a `FakeCounter` — which is the real 53220A driver over a
  simulated VISA session — alternating `set_threshold` and `count` against
  different simulated detectors. Each run's counts must match what it would get
  alone. Plus the refusals: a root claim while either channel is held, a
  `configure_gate` from a channel holder, and a write with no token to a held
  channel.

---

## Remaining

- **6.3** push notifications (still polling)
- **8.6** force `attribute` references in the rule builder
- Per-attribute source selection in `select_instruments` — `CompositeResources`
  can route, but the generator does not yet emit `instrument_sources`
- Read-only mode / explicit edit destination for a merged multi-source tree
- **Phase 9** — run-scoped claims, threaded dispatch, and lease acquisition for local runs
