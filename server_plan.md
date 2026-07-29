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

## Phase 2 — One hardware owner

- **2.1** Dual-bind one ROUTER socket (`ipc://` + optional `tcp://`). No second
  process.
- **2.2** Wizard find-or-start for its workspace server. Managed mode already
  exists in `server_control.py`.
- **2.3** Route wizard hardware ops through the session; remove in-process
  `create_inst()` from the discover / apply-children paths. **This is what makes
  the permission gate honest.**
- **2.4** Registry lifecycle: `register` / `unregister`, eviction, and
  `disconnect()` in dependency order (children before roots). Also closes the
  roadmap's "graceful shutdown" item.
- **2.5** Per-root concurrency. Once everything funnels through the server, one
  slow `set_voltage` blocking all RPCs becomes a real problem. One lock per
  `exclusive` root; `shared` roots run parallel.
  **The lock must wrap the transaction, not the RPC** — `query_instrument` is
  write-then-readline, and interleaving inside it is the documented Prologix
  desync bug (`.claude/prologix_scan_slowness.md`).

---

## Phase 3 — DBay live state

DBay's GUI backend is a `LabSync` reactive server with versioned snapshot +
patch broadcast.

**How much glue is actually needed.** `AsyncLabLinkClient._handle_patch` applies
each incoming patch to its own snapshot *before* invoking callbacks, so
`client.snapshot()` is always current. Lab Wizard never parses JSON pointers,
never applies patch ops, never reassembles state. What lab-link does *not* have
is a client-side **typed** mirror — `_snapshot` is a plain dict; `ReactiveModel`
is the server-side authoring primitive (wrong direction) and `StateStore` is
server-internal. So the mirror is ours, and it is small:

```python
class MirroredState(Generic[T]):
    def current(self) -> T:
        if self._client.state_version != self._version:
            self._cached = self._cls.model_validate(self._client.snapshot())
            self._version = self._client.state_version
        return self._cached
```

- **3.1** ~~Add `on_patch` / `on_snapshot` passthrough to `GuiSync`~~ —
  **done, shipped in dbay 0.5.0** as `DBayClient.on_patch()`,
  `on_snapshot()`, and `state_version`, plus the `GuiSync` passthroughs. Both
  connect on demand and return an unsubscribe callable.
- **3.2** For `state_authority == "subscribed"` roots, the server opens the sync
  connection at registry build (websocket only — no hardware) and subscribes.
- **3.3** **Declarative field map**, not patch parsing: declare which mirror
  fields feed which state keys — DBay's `data[slot].vsource.channels[i].activated`
  → `(inst://<root>/<child>/channel/<i>, "output")`. Lives next to the existing
  `_state_methods_` declarations.

  This mapping is irreducible: it bridges two *different domain models* (DBay's
  module tree vs Lab Wizard's `inst://` + state-key vocabulary). No sync library
  can infer it, and you don't want it gone — it **is** the vendor-neutral
  abstraction that lets a rule say "this VSource is biased on" without knowing
  DBay exists.
- **3.4** Skip echoes of our own writes via `origin_client_id`; detect dropped
  updates via `state_version` → force resnapshot.
- **3.5** Seed `StateTracker` from `snapshot()` at connect rather than
  `state_defaults`. On any resync, fail to the **safe** belief
  (`activated=False`), not the last-known one — following DBay's own
  `_reset_transient_flags` precedent.
- **3.6** `StateTracker.record` becomes a no-op for subscribed paths. Inferring
  state you are being told is a correctness bug, not a shortcut.
- **3.7 Locking.** lab-link callbacks fire on the client's own thread while
  `StateTracker` is touched from the ZMQ serve loop. `StateTracker` has no
  locking today because it has never needed any. It will.

Net effect: someone drags a slider in the DBay GUI and the safety gate knows
within a round-trip.

### Outstanding upstream item

`client/dbay/state.py` ships only `Core` / `IModule` / `Empty` — a stub. The real
models (`SystemState`, per-module state) live in `gui/backend/backend/state.py`
and are not distributed. Until DBay exports its state schema from the client
package, 3.3 validates into hand-written types that duplicate that schema and can
drift silently.

**Shipping the state schema from the client package** is the fix, and it is the
"reactive server whose clients get its schema, not just its bytes" move. It is an
architectural change (where does the shared schema live — client package,
separate schema package, or backend importing from client?) and needs a decision
before anyone writes code.

> **Risk:** 3.3 remains the only item whose difficulty can't be estimated from
> the outside. Prototype against `dac4D` before committing to the phase.

---

## Phase 4 — Tree visibility, multi-source, mixed projects

- **4.1** `tree.get()` over the wire, mirroring what `/api/manage-instruments`
  returns today. Read-only.
- **4.2** Server also serves its **schema / vocabulary** — instrument metadata,
  discovery actions, per-class `state_keys` / `methods` from
  `permissions_api.py`. These depend on the *server's* installed version, which
  can differ from the client's. Server sends data + schema; client renders.
- **4.3** Keep `list_attributes` as the narrow measurement-binding contract.
  Don't conflate it with the admin tree view.
- **4.4 Machine-local server registry.** `~/.lab_wizard/servers/<workspace-hash>.json`
  holding `{bind, pid, workspace_path, started_at}`. Written on start, removed on
  stop, stale entries reaped by `_pid_alive`.

  This is required because the ipc socket path is hashed on the *server's*
  workspace — a GUI opened in a different workspace on the same machine cannot
  derive it. Makes "search localhost at startup" a directory read rather than a
  port scan, and tells you which workspace each server belongs to so the merged
  tree can label it. **Never scan ports.**
- **4.5 `CompositeResources` + per-attribute ownership.** The real prerequisite
  for mixed local/server measurements.

  ```python
  class CompositeResources:
      """Routes from_attribute(name) to whichever source owns that attribute."""
  ```

  Holds one local `ResourceConfig` plus N `RemoteResources`. Small, because both
  already expose an identical `from_attribute(name)` interface by design
  ([model_tree.py:49](lab_wizard/lib/utilities/model_tree.py#L49)).

  Record the owner per attribute in the project YAML. **`--remote` then stops
  being needed as a global flag** — routing lives in config, where it is
  inspectable and reproducible, instead of in a command-line argument someone
  forgets.
- **4.6** Detect divergence between config on disk and the server's in-memory
  tree. The server snapshots at boot; hand-edits (git pull, text editor) will
  still happen. Without this, the wizard shows server state, the file says
  something else, and `git diff` shows changes nobody made through the UI.

---

## Phase 5 — UI

All three tree pages import the same
[TreeNode.svelte](lab_wizard/wizard/frontend/src/lib/components/TreeNode.svelte),
and all four tree-bearing pages source from one endpoint
(`/api/manage-instruments`, or `/api/permissions` which embeds `tree`).
**Keep that contract and change only what's behind it** — the swap then
propagates everywhere for free.

### The merged tree

Show roots from every source at once: local files, plus every reachable server
(from 4.4's registry and `servers.yaml`). Group and badge by owner.

The constraint is **not** on the view — it's that every mutation must name its
target. The thing to avoid is an "add instrument" button with no visible answer
to "add where."

- **5.1** Owner grouping + provenance labels. Non-negotiable — without it, "why
  didn't my edit show up" is unanswerable.
- **5.2** Per-node badges from 1.2: `exclusive`/`shared`, `held`/`free`.
- **5.3** Read-only mode with an explanation, when a remote server hasn't opted
  into `tree.*` writes (8.3).
- **5.4** Explicit destination for every add/init — either implied by the group
  you acted within, or an explicit picker.
- **5.5** `select_instruments`: the "Available on remote servers" info box
  ([+page.svelte:531-552](lab_wizard/wizard/frontend/src/routes/select_instruments/+page.svelte#L531-L552))
  becomes **selectable options in the same dropdown as local ones**, tagged with
  their server. Net UI *reduction* — the local/remote duality collapses.
- **5.6** Server connection state + local override. Auto-use the workspace
  server if one is running; show it in the provenance indicator. The local
  override exists but is deliberate and explained — choosing local means opening
  hardware in-process, which defeats the gate and risks a resource conflict.
  That warrants a confirmation naming both consequences, surfaced on demand, not
  a startup modal everyone learns to dismiss.
- **5.7** Staleness handling. `+page.ts` loads once; with multiple clients on one
  server that's wrong. Needs Phase 6.3 push, or at minimum a refresh affordance.

### Mixing rules (what 5.4 must enforce)

| Situation | Allowed? |
|---|---|
| Client adds a **shared** root locally (DBay GUI mode) while server also serves it | **Yes** — both are clients of the DBay daemon |
| Client adds an **exclusive** root locally that the server **holds** | **No** — refuse at authoring time |
| Client adds an **exclusive** root the server has **configured but not resolved** | Yes, with a lease — but warn; starting the server's copy will now fail |
| Client adds any root the server doesn't know about | Yes, unconditionally |

---

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
