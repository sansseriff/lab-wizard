# Guards: a client that knows what "safe" means keeps its hold

> Status: proposed. Builds on [server_plan.md](server_plan.md) Phase 9 (claims)
> and Phase 7 (leases).

Some instruments must never be touched by anyone but the program watching over
them. Take cryo amplifiers biased by a SIM928, and a pulser that must not fire
while they are on. Today nothing guarantees that, for three reasons:

- The server releases a rack whenever it is asked to, and nothing
  tracks whether anyone still cares about that rack.
- Once a rack is released, a script that opens the port itself bypasses the
  permission gate completely.
- When a claim expires, the server **restores baseline**. That is an action
  the server takes on the hardware, based on a definition of "safe" the server
  cannot know. For a charged magnet, a zero setpoint could be the dangerous
  state.

The answer this plan takes: **the client that understands the experiment
decides what is safe; the server makes sure nobody interferes with that
client's hold.**

---

## 1. The principle

**On its own initiative, the server only does things that leave the hardware
alone.**

| Server action | Changes hardware? | Server may do it on its own? |
|---|---|---|
| Refuse a call | no | yes |
| Keep a port open | no | yes |
| Freeze a path (refuse all writes to it) | no | yes |
| Report state as unknown | no | yes |
| Restore baseline | **yes** | only when the holder asked for it |
| Release a port, letting another process open it | effectively yes | only for paths no one guards |

Any action that changes hardware is a decision about safety. Only a client that
knows the experiment can make that decision, so the server needs that client's
instruction before taking one.

## 2. What already exists

Claims ([claims.py](../lab_wizard/lib/server/claims.py),
[wire.py `claim_*`](../lab_wizard/lib/server/wire.py)) cover most of this:

- An exclusive hold on a subtree, all-or-nothing, that widens to the unit
  the instrument can be claimed as.
- Liveness by renewal. [`RemoteClaim`](../lab_wizard/lib/client/claims.py)
  renews every `ttl_s / 3`.
- `release` refuses while a claim touches the path
  ([wire.py `release`](../lab_wizard/lib/server/wire.py)). Tree edits refuse
  on held racks (Phase 8).
- Leases (`~/.lab_wizard/leases/`) make the server refuse to open a transport
  that a process opening it directly has taken.

Where claims fall short of being a guard:

1. **Expiry resets and frees.** `_restore_baseline` runs, then the units
   become free. A guard needs the opposite: freeze and hold.
2. **Claims are not kept on disk.** A server restart forgets them
   (`claim_renew`: "...or the server restarted"). Once the server is back
   up, a script that opens the port itself gets the rack.
3. **A claim blocks other runs completely.** A cryo monitor wants to forbid
   *some* writes, not every one. A second run should still be able to use
   the SIM928's other channels, or to set the bias while it is off.
4. **Nothing stops the holder's port from being lost.** Stopping the server,
   and idle release (if added), do not check claims.
5. **The permission gate fails open on unknown state.** A condition with
   `equals: "on"` is false when the state is unset
   ([permissions.py](../lab_wizard/lib/server/permissions.py)), so the rule
   does not fire. Any state a guard depends on must deny when unknown.

## 3. Design

### 3.1 A guard is a claim with different expiry behavior

A new kind of claim, not a new subsystem. A guard keeps a claim's exclusion
rules, token, renewal and audit events, and adds four things:

```
claim_acquire(paths, holder, ttl_s, kind="guard", on_lapse="freeze")
```

- `kind="run"` (default) works exactly as today.
- `kind="guard"` requires a same-machine (ipc) peer for now. A guard is a
  strong hold, and Phase 8 already reserves strong authority for ipc.
- `on_lapse`: `"freeze"` (the only value for guards) or `"restore"` (today's
  behavior, the default for runs). It is a field rather than being implied
  by `kind` because a run may want to freeze too, and that choice belongs to
  the holder.

### 3.2 States

```
          acquire                 renewal missed
  (none) ────────▶ GUARDED ──────────────────────▶ LAPSED (frozen)
                     ▲  │                               │
         resume(token)  │ release(token)                │ force_release(ack)
                     │  ▼                               ▼
                     └─ LAPSED                       (none)
                        (none) ◀────────────────────────┘
```

- **GUARDED.** The holder's token writes freely. Writes from anyone else
  follow §3.4. The server keeps the transport open. Release, tree edits,
  stopping the server, and idle release refuse, naming the guard.
- **LAPSED (frozen).** The heartbeat stopped. Nothing is reset. The transport
  stays open and every write to the guarded paths is refused, from anyone,
  including the old token until it resumes. Reads still work, so a person
  can see the hardware's state. A `guard.lapsed` event is recorded and shown
  prominently (§3.7).
- **resume(token).** The guard's process came back with the same token
  (it saved the token). It returns to GUARDED. A restarted monitor resumes
  rather than re-acquiring, so a crash-and-restart never leaves a window
  where the rack is free.
- **force_release(unit, acknowledge=...).** A person decides. This requires
  an explicit acknowledgement string, is recorded with the actor, and
  releases **without** restoring baseline. The person who forced it
  decides what happens next.
- **release(token).** A clean shutdown by the guard itself. If its
  `on_lapse` was `restore`, it restores; a guard never does. A guard that
  wants its hardware left safe on exit makes those calls itself before
  releasing, since it knows what safe means.

### 3.3 Kept on disk across restarts

Guards are written to `config/server/.guards.json`: token hash, holder,
units, ttl, state. They are written on acquire, release and lapse, and
removed on release. On boot the server:

1. Loads every guard as **LAPSED** (no heartbeat has arrived yet).
2. Opens each guarded transport straight away, so a script that opens the
   port directly cannot get it in the gap. This is the one place the server
   opens hardware with no caller. It is consistent with §1: opening a port
   does not change hardware state. (Check per transport: some devices reset
   when the port opens, e.g. a DTR toggle. Such a transport declares that,
   and is left closed but leased.)
3. Records each guarded path's state as **unknown** (§3.5) until the guard
   resumes and reports it.

The guard resumes with its saved token, and the server checks the token
against the stored hash.

### 3.4 Writes from others: the guard sets the policy

A guard does not need to block every write; it needs to decide which writes
are allowed. That can be done in two stages:

**Stage A: exclusive.** While guarded, a write from any other token is refused
(today's claim rule). Simple, correct, and probably sufficient for a first
version.

**Stage B: the guard supplies rules and state.** The guard provides:

- **State.** The guard is the authority for its paths' state, using the
  mechanism DBay already uses ([external_state.py](../lab_wizard/lib/server/external_state.py),
  `StateTracker.set_external`). Recorded state then comes from the process
  that actually knows it, not from the server's inference.
- **Rules.** `guard_set_rules(token, rules)` installs permission rules
  scoped to the guard. They are evaluated with the server.yaml rules and
  removed when the guard is released (but kept while it is lapsed, which
  also freezes everything).

Other runs may then claim and write to guarded paths, and the permission gate
enforces the guard's own idea of safe. This is the "trust the expert client"
model, without a round trip per call.

**Stage C: ask the guard before each write** (rejected for now). The server
would ask the guard before dispatching each write to guarded paths. It is the
most general option, but every write would pay a round trip, and a guard that
is slow or lapsed would block all writes. Revisit only if Stage B's rules
prove unable to express a real case.

### 3.5 Unknown state denies

Separate from guards, needed by them, and worth doing first:

- A `when` condition that reads unknown state makes the rule **fire**
  (deny), for `equals` and `not_equals` alike. A rule that is meant to
  allow calls while the state is unknown must say so (`unknown: allow`).
- Released or lapsed paths have their recorded state cleared to unknown.
  Once the port has been free, anything may have touched the hardware.
- Error messages say "state unknown" rather than naming a value, so a
  refusal never claims something about the hardware the server does not
  know.

This tightens existing behavior. Existing rules will deny more on a fresh
server until state has been set. That is the intent, but say so in the
release notes, and pre-fill state from `state_defaults` as today.

### 3.6 Places that must check guards

| Place | Today | With guards |
|---|---|---|
| `wire.release` | refuses while a claim touches it | same; the message names the guard |
| Tree edits (Phase 8) | refuse on held racks | guarded racks are held, no change |
| `server_control.stop_server` / restart | unconditional | refuse while any guard is GUARDED or LAPSED, unless `force=True` with acknowledgement; on a forced stop the guards stay on disk |
| Idle release (future) | not built | skips guarded and claimed transports |
| Direct access (`LocalTransportClaim`, preflight) | refuses if the server holds the root | unchanged: guarded roots are always held. The message names the guard |
| Generated custom resources | **do not take `LocalTransportClaim`** | fix: emit it like embedded measurements do (bug from 2026‑10‑02, errno 35) |
| `HeldHardwareWarning` (post-generation) | offers Release | hides Release for guarded roots; shows who guards them and since when |

### 3.7 What a person sees

- **Hardware & Servers:** a Guards section listing holder, units, state
  (GUARDED or LAPSED), last heartbeat, and since when. A lapsed guard is a
  `crit` callout with "Force release…", which opens a confirmation dialog
  where the person types the unit name.
- **Server status / sidebar:** a lapsed guard is a persistent alert, not just
  a log line. A frozen rack with nobody aware of it is the failure mode
  here.

### 3.8 The guard client

A library class, so writing a monitor is a few lines:

```python
from lab_wizard.lib.client.guard import Guard

with Guard(
    server,                       # ipc endpoint, or Session
    attributes=["cryo_amp_bias"],
    holder="cryo-monitor",
    token_file="~/.lab_wizard/guards/cryo-monitor.token",   # for resume
    ttl_s=5.0,
) as guard:
    guard.set_rules([...])        # Stage B
    while True:
        state = guard.resources.cryo_amp_bias.get_output()   # its own token
        guard.report_state("inst://.../channel/0", output=state)
        render(state)
        time.sleep(1)
```

- Renewal runs on a background thread, as `RemoteClaim` already does. If a
  renewal fails, the guard raises in the main thread rather than carrying on
  without a hold.
- **The guard watches the server too.** If the server stops answering, the
  guard alarms however the program chooses (a UI banner, a sound, an email).
  If the server dies, all its holds die with it. Only the guard can notice
  that.
- Resume is automatic: `__enter__` resumes from `token_file` if one exists.

---

## 4. Out of scope

- **Processes that do not cooperate.** Raw pyserial without `exclusive=True`,
  NI-MAX, an unplugged cable. As server_plan §2.5 says: cooperative, not
  airtight. The guard makes honest mistakes impossible to make silently. It
  does not stop someone who deliberately goes around it.
- **Replacing hardware interlocks.** For states that really could injure
  someone or wreck equipment, a hardware interlock is still the backstop.
  A guard prevents software accidents; it is not a safety system.
- **Guards from other machines (tcp).** Possible later. Needs an answer to
  "who may freeze my hardware" that ipc's same-machine rule gives for free
  today.

## 5. Phases

1. **Unknown state denies** (§3.5). Small, standalone, and every later step
   depends on it. Includes clearing recorded state on `release`.
2. **Generated custom resources take `LocalTransportClaim`.** Small; fixes
   the errno 35 bug at its source.
3. **`on_lapse="freeze"` and LAPSED** in `ClaimTable` and `WireServer`:
   freeze, resume, force_release with acknowledgement, events. Stage A
   exclusivity. Tests: a lapse never calls `apply_baseline`; a frozen path
   refuses the old token until it resumes; force_release records the actor
   and does not restore.
4. **Kept on disk and restored on boot** (§3.3), plus the stop/restart guard
   in `server_control`.
5. **`Guard` client class and an example cryo monitor.** An end-to-end test
   against lab-sim: kill the monitor, confirm the rack is frozen, restart it,
   confirm it resumed with no gap.
6. **UI** (§3.7) and `HeldHardwareWarning` naming the guard.
7. **Stage B**: guard-supplied state and rules.
8. **Idle release** of unguarded, unclaimed transports. Now safe whatever the
   experiment, because nothing has said it cares about them.

## 6. Open questions

- **Default `ttl_s` for guards.** Short (seconds) means fast lapse detection
  but a garbage-collection pause can trip it; long means a dead monitor goes
  unnoticed longer. Since a lapse only freezes, a false lapse costs little,
  which argues for short.
- **Should a run whose claim lapses also freeze** instead of restoring?
  Following the principle consistently, maybe yes, with `restore` kept as an
  explicit option a procedure chooses. That changes Phase 9 behavior; decide
  separately.
- **Transports that reset when the port opens** (§3.3 step 2). Is there a
  real one in the current instrument set? If not, leave the declaration out
  until one appears.
- **Guards from other workspaces on the same machine.** ipc already covers
  them. Confirm the Hardware page shows guards from every local server, as
  it does for holds.
