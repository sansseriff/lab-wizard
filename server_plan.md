# Server / client architecture plan

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
| Wizard ([main.py](lab_wizard/wizard/backend/main.py)) | writes `config/instruments/*.yml` | FastAPI + webview; **also opens hardware in-process** |
| Server ([server.py](lab_wizard/lib/server/server.py)) | live instrument handles | reads config at boot, ZMQ ROUTER + JSON-RPC |
| Client ([remote_resources.py](lab_wizard/lib/client/remote_resources.py)) | nothing | typed proxies, no local config by design |

Wire surface is six methods ([wire.py:63-68](lab_wizard/lib/server/wire.py#L63-L68)):
`call`, `list_paths`, `list_attributes`, `describe_path`, `describe_attribute`,
`list_descriptions`. Everything is read-only except `call`.

### Known defects this plan addresses

1. **Two hardware owners.** [main.py:544](lab_wizard/wizard/backend/main.py#L544)
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
6. **Proxies cache a frozen path.** [remote_resources.py:82-88](lab_wizard/lib/client/remote_resources.py#L82-L88)
   caches `name → proxy` with the `inst://` path baked in. Latent bug the moment
   the tree becomes mutable.

### What is *not* broken

The DBay integration is current. [lab_wizard/lib/instruments/dbay/dbay.py](lab_wizard/lib/instruments/dbay/dbay.py)
is on the unified `DBayClient` API and every call site is valid. (The stale
pre-lab-link snapshot under `ref/dbay-backend/` was deleted in Phase 0.2 — it
misled readers about which transport DBay actually uses.)

---

## 2. Core concepts

### 2.1 Declared vs held

The registry is lazy: `_factories` holds everything it *could* serve, `_index`
holds only what it has actually instantiated
([registry.py:284-292](lab_wizard/lib/server/registry.py#L284-L292)).

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
`server_status` ([server_control.py:204-211](lab_wizard/wizard/backend/server_control.py#L204-L211)).

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
| 0.3 | Pass `exclusive=True` to `pyserial.Serial(...)` — converts silent byte-theft into an immediate error | [serial.py:67](lab_wizard/lib/instruments/general/serial.py#L67) |

---

## Phase 1 — Taxonomy + cheap safety net ✅ done

Highest value per line in the plan. No governance decisions.

- **1.1** ✅ `transport_sharing()` / `state_authority()` / `transport_key()` on
  `CanInstantiate` — the roots are exactly the things that open a transport.
  Types and conservative defaults in
  [`instruments/general/transport.py`](lab_wizard/lib/instruments/general/transport.py).
  Implemented for Prologix (serial), DBay (mode-dependent), Keysight 53220A and
  Yokogawa AQ2212 (single-session sockets). SIM900 needed nothing — it is a
  *child* of the Prologix bus and inherits its root's transport.
- **1.2** ✅ `registry.transport_for()` resolves any path to its root's
  declarations, and `describe_path()` carries them. Roots that duck-type
  `create_inst` without inheriting `CanInstantiate` fall back to the safe
  default rather than failing to register.
- **1.3** ✅ `registry.list_held()` / `held_roots()` / `exclusive_roots()`, and
  a `list_held` RPC returning all three. Makes declared-vs-held visible.
- **1.4** ✅ [`client/preflight.py`](lab_wizard/lib/client/preflight.py), wired
  into both setup templates.
  [`client/server_discovery.py`](lab_wizard/lib/client/server_discovery.py)
  finds the workspace's bind by reading `server.yaml` — a file read, never a
  port scan. An unreachable server is not an error: local projects must still
  run when none is up.
- **1.5** ✅ [`wizard/backend/transport_status.py`](lab_wizard/wizard/backend/transport_status.py)
  behind `GET /api/transport-status` and `POST /api/transport-status/check`.
  Reports `held_conflicts` (local run fails now) separately from
  `configured_conflicts` (works now, breaks the moment the server touches that
  rack), and flags `duplicate_transports` where two roots resolve to one
  device.

Covered by [`tests/test_transport_sharing.py`](tests/test_transport_sharing.py)
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
  [`wizard/backend/hardware_access.py`](lab_wizard/wizard/backend/hardware_access.py)
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

Covered by [`tests/test_single_hardware_owner.py`](tests/test_single_hardware_owner.py)
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
- **3.2** ✅ [`server/external_state.py`](lab_wizard/lib/server/external_state.py).
  `attach_external_state()` subscribes every root declaring
  `state_authority() == "subscribed"`; `DBayParams.state_authority_client()`
  builds a read-only client (`load_state=False`) distinct from the command one.
- **3.3** ✅ [`instruments/dbay/state_sync.py`](lab_wizard/lib/instruments/dbay/state_sync.py)
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

Covered by [`tests/test_external_state.py`](tests/test_external_state.py) (15 tests).

## Phase 4 — Tree visibility, multi-source, mixed projects ✅ done

- **4.1** ✅ `tree_get` RPC — same shape `/api/manage-instruments` returns, plus
  per-root transport facts and `held_roots`. Refuses in single-project hosting
  mode, which has no editable tree.
- **4.2** ✅ `schema_get` RPC — instrument metadata and permission vocabulary
  come from the **server's** build, which can differ from the client's. Server
  sends data and schema; the client renders.
- **4.3** ✅ `list_attributes` left narrow, as the measurement-binding contract.
- **4.4** ✅ [`client/server_registry.py`](lab_wizard/lib/client/server_registry.py).
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
- **4.5** ✅ [`client/composite_resources.py`](lab_wizard/lib/client/composite_resources.py)
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

Covered by [`tests/test_server_registry.py`](tests/test_server_registry.py)
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

## Phase 6 — Client robustness

- **6.1** Proxy re-resolution by `attribute_name` on path error. Fixes the frozen
  path cached in `RemoteResources._proxy_cache`.
- **6.2** `Session` reconnect / backoff.
- **6.3** Server→client push (tree changed, state changed, lease changed).
  Requires a receive thread — same work item as 6.2, do them together.

> **Strategic note.** lab-link already solves versioned snapshot + patch
> broadcast, command/ack with structured errors, reconnect, and sync+async
> clients — four roadmap items. Lab Wizard hand-rolls the same thing on
> ZMQ + pyleco.
>
> Don't rewrite now: ZMQ `ipc://` beats websockets for same-machine latency, and
> pyleco's binary frames are the right path for the roadmap's bulk-array item,
> which JSON-over-websocket handles badly. But **design 6.3 to mirror lab-link's
> semantics** (versioned snapshot + patch, `origin_client_id`, command/ack) so
> the two systems feel identical to users and converging later is cheap.
> Revisit after Phase 3, once lab-link has been consumed in anger.

---

## Phase 7 — Leases

Only build when "server, give that rack back" is actually needed. Phases 1.3–1.5
cover the common case without any crash-recovery edge cases.

- **7.1** Claim registry keyed on transport identifier (see 2.3).
- **7.2** Server is authority when running; workspace lockfile when not — which
  incidentally fixes two *local* experiments colliding, a problem with no
  mitigation today.
- **7.3** pid-liveness for stale claims.
- **7.4** Server reads claims at boot; declines to `resolve` leased roots but
  still lists and describes them (static metadata, no hardware).

---

## Phase 8 — Tree writes + governance

Last, deliberately. By this point Phases 1–5 will have made the right answers
substantially more obvious.

- **8.1** `tree.add/remove/reset/update/discover` — mostly re-hosting
  `config_io` functions, which are already pure over `config_dir`. Server writes
  through `save_instruments_to_config` so the git-able YAML audit trail is
  preserved. **This is a requirement, not an implementation detail.**
- **8.2** Capture client identity — already on the wire and discarded at
  [wire.py:172](lab_wizard/lib/server/wire.py#L172).
- **8.3** Authority default: `ipc://` peers get full `tree.*`; `tcp://` peers get
  read + `inst.call`, with `tree.*` opt-in per `server.yaml`.
- **8.4** Permission coverage for `tree.*` — **allowlist**, not the blocklist
  model used for method calls.
- **8.5** Fail closed when a rule references a removed attribute: deny
  everything it covered rather than silently dropping the rule.
- **8.6** Force `attribute` references in the rule builder; retire raw `path`.
  Hash-derived paths don't survive key-field edits.

---

## Sequencing

```
0 ──▶ 1 ──▶ 2 ──▶ 3
      │     │
      │     └──▶ 4 ──▶ 5
      │           │
      └──▶ 7      └──▶ 6 ──▶ 8
```

- **0, 1** are days, not weeks, and 1.5 removes most of the day-to-day pain.
- **2** establishes the invariant everything else depends on.
- **3** is self-contained once 1.1 lands.
- **4, 5** are additive; 4.5 must land before 8 because it defines what the
  tree's ownership metadata is *for*.
- **7, 8** are the two phases needing policy decisions, and they're last on
  purpose.

## Open questions

1. **3.3** — how stable is DBay's state shape across module types? Prototype
   first.
2. **DBay state schema export** — where should the shared schema live? Blocks
   3.3 from validating into real types rather than hand-written duplicates.
3. **Direct-UDP DBay** — classified `exclusive` defensively. Worth confirming
   whether reply attribution actually races.
4. **6.3 vs lab-link** — revisit the transport decision after Phase 3.
