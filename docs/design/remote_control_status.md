# Status: Remote Instrument Control for lab_wizard

**Status:** Phases 1–3 built and tested; Phase 4 + several follow-ups outstanding (2026-05-21)
**Audience:** an engineer/agent picking up this work cold.
**Related docs:**
- Original plan: `~/.claude/plans/investigate-this-codebase-key-clever-llama.md`
  (the "Remote Instrument Control for lab_wizard" plan — context, architecture, phases).
- Wizard/permissions UI design: `docs/design/remote_control_and_permissions.md`.

---

## What this feature is

Run a measurement on machine B against instruments physically attached to
machine A, with the user-facing API unchanged. Instead of tunneling raw
SCPI/USB bytes, we do **RPC at the user-API layer**: a **server** on A hosts the
real instrument tree and exposes every method (`set_voltage`, `get_voltage`, …)
over ZMQ + JSON-RPC; a **client** on B holds typed proxies that forward calls.
A **permission state machine** on the server blocks unsafe calls (e.g. "don't
pulse while the cryo bias is on").

Read the original plan for the full rationale (why not a wire-level transport
swap, the asymmetric config-ownership model, the proxy strategy, etc.).

---

## Architecture as built

```
machine B (client / consumer)            machine A (server / host)
─────────────────────────────           ───────────────────────────────
RemoteExp.connect(url)                   WireServer (ZMQ ROUTER)
  .from_attribute("bias", as_type=…)       ├─ InstrumentRegistry  (inst:// → live object,
     → describe_attribute RPC               │     attribute_name → path)
     → typed proxy (RemoteVSource/…)        ├─ PermissionGate      (rules + StateTracker)
  proxy.set_voltage(0.5)                     └─ real instrument tree (Parent/Child/channels)
     → call("inst://…","set_voltage",[0.5])
        ── ZMQ DEALER ──▶  ROUTER ──▶ gate.check ▶ getattr(obj,m)(*a) ▶ gate.record
        ◀── JSON-RPC result / error ──
```

- Wire format = pyleco `Message` (multipart, conversation_id) + JSON-RPC 2.0 via
  pyleco `RPCServer`. **We do not use pyleco's `MessageHandler`/`Coordinator`**
  (that's a Coordinator-client topology); we bind a ROUTER directly. Migrating
  to a Coordinator later is additive (wire format is identical).
- Permission denials are JSON-RPC error **code -32001** carrying
  `{rule_id, blocking_state}`; the client raises `PermissionDeniedError`.

---

## What is built (Phases 1–3)

### Server — `lab_wizard/lib/server/`
| File | Role | Notes |
|------|------|-------|
| `__init__.py` | package | |
| `registry.py` | `InstrumentRegistry`: eager tree walk, `inst://` index, `attribute_name` index, `describe_path/attribute`, `list_*` | Built from an **`Exp`** today (see deviation #1). `_BEHAVIOR_ABCS` priority: VSource > VSense > ChannelProvider. |
| `wire.py` | `WireServer`: ROUTER loop, pyleco framing, `RPCServer` dispatch, gate integration | RPCs: `call`, `list_paths`, `list_attributes`, `describe_path`, `describe_attribute`, `list_descriptions`. `PERMISSION_DENIED_CODE = -32001`. |
| `permissions.py` | `Condition`/`DenyClause`/`Rule`/`PermissionsConfig`, `StateTracker`, `PermissionGate`, `load_permissions` | Rule-list + declarative-state model (not an FSM). |
| `server.py` | CLI entry: load config → build registry + gate → serve; SIGINT/SIGTERM stop | Run: `python -m lab_wizard.lib.server.server --config <server.yaml>`. |
| `demo_client.py` | tiny DEALER client for manual poking | Not part of the library surface; smoke-test aid. |
| `_smoke_test.py` | in-process server+client+proxies, no hardware | `python -m lab_wizard.lib.server._smoke_test`. |

### Client — `lab_wizard/lib/client/`
| File | Role | Notes |
|------|------|-------|
| `__init__.py` | exports `RemoteExp`, `Session`, `RemoteCallError`, `PermissionDeniedError` | |
| `session.py` | `Session`: sync DEALER, one-call-at-a-time lock, error→exception mapping | `PermissionDeniedError(RemoteCallError)` for code -32001. **No reconnect yet.** |
| `remote_exp.py` | `RemoteExp.connect(url)`, `from_attribute(name, as_type=…)` (typed overload), `list_attributes`/`describe_attribute`/`list_descriptions` | Proxies cached per name. **No `from_config`** (remote uses `from_attribute` only — by design). |
| `proxies/base.py` | `RemoteProxy` mixin: `__init_subclass__` auto-forwards every inherited abstract method + reflective `__getattr__` tail; `RemoteOpaque` fallback | Auto-forwarding is **beyond the original plan** (deviation #3). |
| `proxies/vsource.py` / `vsense.py` | `RemoteVSource(VSource, RemoteProxy)` / `RemoteVSense(VSense, RemoteProxy)` | One-liners thanks to auto-forwarding. |
| `proxies/registry.py` | `behavior_abc` string → proxy class; `proxy_class_for(...)` | New ABC = one line here. |

### Instrument-layer + config
- `lab_wizard/lib/instruments/general/state_effects.py` — **new.** `Arg`/`Kwarg`/
  `Result`/`resolve_state_value`/`collect_state_methods`. The `_state_methods_`
  vocabulary lives here (instrument layer) so instruments never import server code.
- `_state_methods_` declared on **`VSource`** (general case: `set_voltage`→voltage,
  `turn_on/off`→output) and inherited by `Sim928`, `StandInVSource`.
  **`Dac4DChannel`** overrides only `turn_on/off`→`voltage=0.0` (it has no separate
  output enable). Merge is per-key across the MRO via `collect_state_methods`.
- `lab_wizard/config/server/server.yaml` — bind + `exp_yaml` + a worked cryo-amp
  `permissions:` block.
- Setup templates `iv_curve_setup_template.py` / `pcr_curve_setup_template.py`
  gained a `--remote tcp://host:port` flag and an `Exp | RemoteExp` signature.
- `lab_wizard/pyproject.toml` — added entry points `lab_wizard_server`,
  `lab_wizard_client` (pyleco was already a dependency).

### Tests (all green: 64 in suite + 1 standalone smoke)
- `tests/test_remote_client_server.py` — typed `from_attribute`, isinstance,
  round-trips, discovery, error cases (8).
- `tests/test_permissions.py` — unit: conditions, deny clauses, StateTracker,
  gate (16).
- `tests/test_remote_permissions.py` — gate enforced over the live wire (5).
- `tests/test_remote_end_to_end.py` — single narrative scenario; also runnable as
  `python -m tests.test_remote_end_to_end` to watch it print each step (1).
- `lab_wizard/lib/server/_smoke_test.py` — Phase 1+2+auto-forward smoke.

Run everything: `python -m pytest tests/ -q`.

---

## Deviations from the original plan (read these — they will confuse you otherwise)

1. **Server hosts a project `Exp`, not `config/instruments`.** The original plan
   and Phase 1 load a project YAML via `server.yaml: exp_yaml`. We have since
   **decided** (see `remote_control_and_permissions.md`) the server should host
   the whole `config/instruments` tree. **This change is not yet made.** It is
   build-order item #1 there.
2. **No `MessageHandler`/`Coordinator`; no `introspect.py`.** Wire is a raw
   ROUTER + pyleco `Message` + `RPCServer`. The planned `introspect.py` was
   folded into `registry.py` + `wire.py`.
3. **Auto-forwarding proxies + `as_type`.** Proxy classes are now empty
   one-liners; `RemoteProxy.__init_subclass__` generates forwarders for inherited
   abstract methods and clears `__abstractmethods__`. `from_attribute` has a typed
   `as_type=` overload (a `typing.cast`-style static hint). Neither was in the
   original plan; both came out of the detour discussion.
4. **`from_attribute` only on the client.** No `RemoteExp.from_config` /
   `RemoteChannelProvider`. Remote projects reference instruments by
   `attribute_name`; channel access via `channels[i]` was intentionally dropped
   (name the channel instead).
5. **`--remote` lives in the setup *templates*, not in wizard codegen.** The
   planned third `generation_style == "remote"` in
   `wizard/backend/project_generation.py` was **not** added; instead the
   templates' `__main__` blocks branch on a `--remote` CLI flag. Newly generated
   projects inherit this automatically.
   - ⚠️ Gotcha already hit and fixed: do **not** add `from __future__ import
     annotations` to a setup template — it stringifies the `Resources` dataclass
     annotations and breaks `get_measurements._extract_resources_from_template`
     (which reads `__annotations__` raw). See git history / that function.
6. **CLI entry points, not a `lab_wizard server` subcommand.** Use
   `python -m lab_wizard.lib.server.server` or the `lab_wizard_server` script.
7. **`Sim970Channel` did not get `_state_methods_`.** It's a `VSense`; reading a
   voltage doesn't change state, so it has nothing to declare. (The plan listed
   it; skipping was correct.)

---

## What remains

### A. Original Phase 4 — "Polish" (not started)
1. **Concurrency** (`lib/server/concurrency.py`, planned, not built). Today the
   server is a single-threaded poll loop: one slow `set_voltage` blocks all RPCs.
   Goal/contract: **same connection → serialized in arrival order; different
   connections → parallel.** This is what enables the key use case of *one server
   sharing a single VISA/serial connection across multiple client programs* (you
   can't open two sessions to one VISA resource, so the server multiplexes it).

   **Critical: serialize per *shared connection*, not per leaf object.** In
   lab_wizard the hardware transport lives at the **root** instrument and is
   shared down the subtree (Prologix root owns the serial port; DBay root owns
   the HTTP client; all channels/modules under a root share it). So two channels
   of one VISA instrument share ONE connection. Keying a lock per channel/leaf
   would let them issue concurrent I/O on the same session → interleaved bytes,
   misrouted responses, corruption. Key the serialization on the **root path
   segment** (`inst://<root_key>`), i.e. the shared transport identity.

   The lock/serialization must wrap the **entire method call**, not each wire
   write — a single operation can be a stateful multi-step SCPI sequence
   (`INST:NSEL 1; VOLT 0.5`) that must be atomic w.r.t. other clients.

   Two valid implementations:
   - **(A) `ThreadPoolExecutor` + per-root `threading.Lock`.** Simple. Caveats:
     operations on one bus may hop pool threads (serialized, but not pinned), and
     a plain `Lock` is **not FIFO** — a later command can overtake an earlier
     waiter.
   - **(B) one dedicated worker thread + FIFO queue per root/connection**
     (recommended for instrument I/O). Gives same-thread affinity (safer for
     finicky VISA backends) and arrival-order fairness for free; different buses'
     workers still run in parallel. Map path → bus worker by root segment.

   Why the thread pool *helps* the multi-client case rather than hurting it:
   without it, one client's long sweep on bus X freezes every client including
   those targeting bus Y; with per-connection serialization + parallel buses, X's
   clients serialize (unavoidable — one connection) while Y stays responsive.

   Wire whichever design into `WireServer._handle_one` / `call`. Re-entrancy is
   not a concern: the lock is taken once per RPC in `call`; in-process
   method→method calls don't pass back through `call`.
2. **Graceful shutdown ordering.** `server.py` currently stops the loop and closes
   the socket but does **not** close/disconnect instruments. Add ordered teardown
   (children before parents; transports released last). Some instruments have
   `disconnect()`; many rely on RAII (`__del__`/`atexit`).
3. **Client reconnect with backoff.** `Session` is a one-shot connect that raises
   `TimeoutError` on no reply. Add reconnect + exponential backoff; preserve
   `inst_path` references and proxy cache across reconnects.
4. **`reload-permissions` hot-swap.** Re-read `server.yaml`'s `permissions:` and
   replace the gate's `PermissionsConfig` without dropping connections or losing
   `StateTracker` state. Expose as an RPC and/or CLI subcommand.
5. **Docs:** one-page admin guide for `server.yaml`; one-page user guide for
   `--remote`. (These two design docs partly cover it; user-facing how-tos still
   needed.)

### B. Original verification items still open
6. **Bulk-data binary path.** `wire.py` returns JSON only. The plan calls for big
   array returns (e.g. a future `TimeTagger.get_counts()`) to ride pyleco's
   `additional_payload` binary frames instead of base64-in-JSON. `Message`
   supports it (`additional_payload=`); `RPCServer.register_binary_rpc_method`
   exists. Wire a binary return path for large payloads.
7. **API parity test.** No automated test runs a real measurement (`iv_curve`)
   both locally and via `--remote` and compares output. Would need a full
   stand-in instrument tree wired through a project YAML.
8. **Tree/attribute parity check.** Original plan wanted the client to cross-check
   the server's exposed set at session start and fail loudly on mismatch. In the
   asymmetric model the natural form is: client verifies the `attribute_name`s it
   needs exist (via `list_attributes`) before running. Not implemented.
9. **Concurrency test** (depends on #1): a slow call on bus X must not block a
   call on bus Y; same-bus calls stay ordered.

### C. Follow-ups from the detour (designed, not built)
These are detailed in `docs/design/remote_control_and_permissions.md`; summary:
10. **Server hosts `config/instruments`** (deviation #1 above). Build-order #1 there.
11. **Rules reference `attribute_name`** (not raw `inst://` paths): add an
    `attribute` field to `Condition`/`DenyClause`, resolve via the registry's
    attribute index. Hashes are volatile (`validate_and_repair_hashes`,
    `config_io.py:451` / `model_tree.py:226`); `attribute_name` is the stable
    handle.
12. **Default attribute-name generation** — hybrid `{type}-{petname}` (e.g. via
    `coolname`) in `wizard/backend/attribute_name_autogen.py`.
13. **Manage Permissions wizard page** + backend endpoints (list local
    instruments with their `collect_state_methods` state keys + method lists;
    read/write the `permissions:` block).
14. **Remote-server consuming flow**: `config/remote/servers.yaml`, live
    discovery during measurement creation, generated `RemoteExp.connect(...)`
    setup. Independent track.

### D. Known open design questions (not blocking, decide when relevant)
- **Unify the per-ABC proxies into one dynamic `RemoteInstrument`?** Discussed and
  shown feasible (server reports the list of behavior ABCs; client builds a class
  with those bases on demand). Deferred — user chose to keep per-ABC proxies for
  now. Revisit before adding the 3rd/4th behavior ABC.
- **`RemoteCapable` opt-in marker** on instruments (capability gate + metadata
  home). Analyzed; not adopted. The mechanism does not require it.
- **Attribute access vs methods on proxies.** `proxy.some_attr` returns a callable
  forwarder (via `__getattr__`), not a value. Users must call getters. If this
  bites, have `describe_attribute` distinguish methods from data attributes.

---

## Suggested next step

If continuing the **hosting/server track**: do C-10 (host `config/instruments`)
first — it underpins the permissions UI and the "list all local instruments"
behavior the user asked for — then C-11 (attribute-name rules) and C-12
(petname defaults), then the UI (C-13).

If continuing the **runtime-robustness track**: do A-1 (concurrency) and A-2
(shutdown) — these matter as soon as the server drives real, slow hardware with
more than one client.

The two tracks are independent. Pick based on whether the immediate goal is
"usable from the wizard" (hosting track) or "robust under real load"
(robustness track).
