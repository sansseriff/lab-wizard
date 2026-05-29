# Task Tree Framework: Design and Implementation Plan

> Companion document: **Lab Measurement Database: Design and Implementation
> Plan**. This document covers *acquisition* (how measurement procedures are
> composed and executed); that one covers *storage*. The seam between them is a
> typed message bus — the framework emits, the database subscribes, and neither
> knows the other's internals.

## Why Replace the Old Action Framework

The old `Action` system worked but was shaped by a constraint that no longer applies. It was a guest inside an external event loop — first the time-tagger's loop, then transitively the PyQt5 draw loop. That forced a specific design:

- Every node had to be a coroutine-like `.evaluate()` that did a tiny slice of work and returned immediately, because it did not own time.
- Long operations (a 10 s integration) had to be expressed as "return `integrating` until enough external ticks have elapsed."
- The tree could not call `time.sleep()`; it was pigeon-holed into polling whether it wanted to or not.
- Data aggregation was bolted onto the control tree (`self.results` flattened upward; `ConcurrentAction(measurement, GraphUpdate)` wired plotting into the tree).

Two of these are worth keeping (composability; periodic intermediate sampling). The rest were accidental complexity imposed by the borrowed loop. **The framework should own a worker thread, control its own time, and emit data rather than accumulate it.**

## Pattern Lineage (What This Already Is)

The design independently reinvented three well-established patterns; naming and practice should align with them.

- **Behavior tree** (robotics / game AI). A tree of nodes, each producing a status; composite nodes aggregate children's status. The old base `Action` running `event_list` in order is exactly a behavior-tree **Sequence**; `ConcurrentAction` is a **Parallel**. ~20 years of practice and standard vocabulary (`py_trees` is the canonical Python implementation).
- **Cooperative coroutine / state machine.** The polling `.evaluate()` that returns `integrating` until done is hand-rolled cooperative multitasking — what `asyncio` formalizes. Kept *only* where periodic work is genuinely needed, not as the universal contract.
- **Dataflow / producer-consumer.** `DependentAction` (output feeds next input) is a pipeline; `ConcurrentAction → GraphUpdate` is producer-consumer. Generalized here into a typed message bus with subscribers.

Converging on these independently indicates the design is sound. The refactor aligns it with mature practice while keeping the genuinely bespoke parts (guest-free worker thread, blocking hardware calls).

## Naming

| Old | New | Rationale |
|---|---|---|
| `Action` | `Step` | "Action" implies an instantaneous act; nodes are long-running, composable units of a procedure. `Step` reads naturally in lab context ("the procedure is a sequence of steps") and avoids `asyncio.Task` collision. |
| `evaluate()` | `run()` | The contract is now "do your whole job; you may sleep; you may be aborted" — blocking, not a polled slice. `run`/`execute` is honest; `evaluate` implied pure computation. |
| (internal) | `_tick()` | Survives privately *only* inside steps that sample periodically (Integrate) and inside `Parallel`. Not public vocabulary. |
| `add_action()` | `add_child()` | Tree vocabulary; makes the structure explicit. |
| bare status strings | `Status` enum | Typo-safe, autocompletes, includes a first-class failure path. |

## Concurrency Model (The Central Decision)

Owning a thread dissolves the old constraint but forces an explicit concurrency choice. The decision:

**Single worker thread. `Sequence` is blocking-sequential. `Parallel` uses cooperative ticking internally. The framework owns one thread, off the Qt thread.**

Reasoning:

- `Sequence` becomes trivial: call each child's `run()` in order; each blocks until done. The old recursion-per-tick hazard disappears entirely.
- Most steps need no ticking. `Wait(10)` sleeps 10 s and returns. `SetVoltage` acts and returns. Only steps that must do periodic work *while running* (Integrate sampling counts every 0.5 s) have an internal loop. Ticks become an implementation detail of long-running leaves, not the universal contract.
- `Parallel` (old `ConcurrentAction`) is the only place needing real concurrency. It is handled by cooperative round-robin `_tick()` over its children within the single thread — **not** a thread per branch. Most lab instruments (GPIB/USB/VISA sessions) are not thread-safe; a thread per branch invites instrument-access races. Cooperative ticking inside one thread avoids that class of bug entirely. Accepted cost: a blocking call in one parallel branch stalls sibling branches; acceptable because the dominant parallel need is "measure while updating a plot," and the plot is a fast non-blocking subscriber, not a parallel branch (see message bus).
- The worker thread must never touch PyQt widgets (Qt is not thread-safe off the GUI thread). The message bus enforces this boundary.

Explicitly **not** adopted: `asyncio` (would mean restructuring around an async loop and `await`-ing blocking hardware calls — the hand-rolled model is correct when blocking instrument I/O dominates); external workflow engines (Airflow/Prefect — wrong scale and failure model for an in-process instrument procedure).

## Step Lifecycle

Every `Step` has an explicit lifecycle, replacing the old `init_time == -1` sentinel hack with named hooks:

- `on_enter()` — called once when the step becomes active. Acquire instruments, set sources, record start time.
- `run()` — do the whole job; blocking; may call `self.sleep()`; must periodically check `self.aborted`.
- `on_exit()` — called once when the step finishes, fails, or is aborted. Release/safe instruments (e.g., ramp voltage to zero). **Runs even on abort.**

`run()` returns a `Status`:

```python
import enum

class Status(enum.Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    ABORTED = "aborted"
```

(`RUNNING` is not a return value here — `run()` blocks until terminal. "Running with periodic work" is internal to the step via `_tick()`.)

Failure is first-class. Lab procedures fail constantly (instrument timeout, source not responding, temperature out of range). A `Sequence` whose child returns `FAILED` stops and propagates `FAILED` (unless wrapped in a retry decorator). Without an explicit failure path, failures either crash the thread or vanish silently.

## Abort and Interruptible Sleep

Owning the thread makes abort *more* important, not less: a `Wait(3600)` calling raw `time.sleep(3600)` is un-abortable for an hour.

- The framework provides `self.sleep(seconds)` — internally waits on a `threading.Event` with a timeout. `abort()` sets the event, waking every sleeping step immediately.
- Steps must use `self.sleep`, never bare `time.sleep`.
- `abort()` propagates down to running children; each child's `on_exit()` still runs so instruments are left safe (voltage ramped down, source disarmed).
- `run()` bodies that loop must check `self.aborted` each iteration.

This is a small mechanism that prevents a major usability and safety defect.

## Base Classes

```python
import enum, threading, time


class Status(enum.Enum):
    SUCCESS = "success"
    FAILED  = "failed"
    ABORTED = "aborted"


class Step:
    """A composable unit of a measurement procedure.

    Lifecycle: on_enter() -> run() -> on_exit(). run() blocks until terminal,
    may sleep via self.sleep(), and must respect self.aborted."""

    def __init__(self, name=None):
        self.name = name or self.__class__.__name__
        self.children = []
        self._abort_event = threading.Event()
        self.context = None          # injected RunContext (params, indices, buses)
        self._node_id = None         # stable path tuple, assigned at execute()
        self._parent_id = None
        self.determinate = False     # override True in steps with a real fraction

    # composition
    def add_child(self, step):
        self.children.append(step)
        return self

    # lifecycle hooks (override as needed)
    def on_enter(self): ...
    def run(self) -> "Status": raise NotImplementedError
    def on_exit(self): ...

    # abort machinery
    @property
    def aborted(self):
        return self._abort_event.is_set()

    def abort(self):
        self._abort_event.set()
        for c in self.children:
            c.abort()

    def sleep(self, seconds):
        """Interruptible sleep; returns True if slept fully, False if aborted."""
        return not self._abort_event.wait(timeout=seconds)

    # progress (emits to the SEPARATE status bus; never the data bus)
    def report_progress(self, fraction, detail=None):
        self.context.status_bus.emit(
            StepProgress(self._node_id, fraction, detail))

    # framework entry point — wraps lifecycle + abort + progress edges
    def execute(self, context, parent_id=None) -> "Status":
        self.context = context
        self._parent_id = parent_id
        self._node_id = (parent_id or ()) + (self.name,)   # path tuple
        self.on_enter()
        context.status_bus.emit(StepBegan(
            self._node_id, parent_id, self.name, self.determinate))
        status = Status.ABORTED
        try:
            if self.aborted:
                return Status.ABORTED
            status = self.run()
            return status
        finally:
            self.on_exit()
            context.status_bus.emit(StepEnded(self._node_id, status))
```

Children are launched with `child.execute(self.context, self._node_id)` so the
path tuple nests automatically — the status bus receives a faithful reflection
of the live tree with zero per-step bookkeeping. `StepBegan`/`StepEnded` are
fully automatic for **every** step (handled here in the base). Only steps with a
meaningful fraction set `self.determinate = True` and call
`self.report_progress(...)`; everything else shows as a brief indeterminate tick.

### Composites

```python
class Sequence(Step):
    """Run children in order. First non-SUCCESS short-circuits."""
    determinate = True   # progress = completed children / total

    def run(self):
        total = len(self.children)
        for k, child in enumerate(self.children):
            if self.aborted:
                return Status.ABORTED
            self.report_progress(k / total, detail=f"step {k+1}/{total}")
            status = child.execute(self.context, self._node_id)
            if status is not Status.SUCCESS:
                return status
        self.report_progress(1.0)
        return Status.SUCCESS


class Parallel(Step):
    """Cooperative round-robin over children within the single worker thread.

    Children must be tick-shaped (implement _tick() returning a Status or None
    for 'still running'). Used for 'measure while doing X' patterns where X is
    light. Heavy/blocking siblings will stall others — by design (see doc)."""
    def run(self):
        pending = list(self.children)
        for c in pending:
            c.on_enter()
        try:
            while pending and not self.aborted:
                for c in list(pending):
                    result = c._tick()        # None => still running
                    if result is not None:
                        pending.remove(c)
                        if result is not Status.SUCCESS:
                            return result
                time.sleep(0)                 # yield
            return Status.ABORTED if self.aborted else Status.SUCCESS
        finally:
            for c in self.children:
                c.on_exit()
```

### Decorators (Sweeps, Retry, Timeout)

Decorators replace nested if-statements and sweep-specific state. The multi-sweep vs single-long-sweep distinction becomes a *composition* choice with no special code.

```python
class Repeat(Step):
    """Run the wrapped step n times. n full passes => n sweeps."""
    determinate = True   # progress = completed passes / n

    def __init__(self, n, child):
        super().__init__(); self.n = n; self.add_child(child)

    def run(self):
        child = self.children[0]
        for i in range(self.n):
            if self.aborted:
                return Status.ABORTED
            self.context.sweep_index = i      # injected into emitted measurements
            self.report_progress(i / self.n, detail=f"sweep {i+1}/{self.n}")
            status = child.execute(self.context, self._node_id)
            if status is not Status.SUCCESS:
                return status
        self.report_progress(1.0)
        return Status.SUCCESS


class Retry(Step):
    """Retry the wrapped step up to n times on FAILED."""
    def __init__(self, n, child):
        super().__init__(); self.n = n; self.add_child(child)

    def run(self):
        child = self.children[0]
        for _ in range(self.n):
            if self.aborted:
                return Status.ABORTED
            if child.execute(self.context, self._node_id) is Status.SUCCESS:
                return Status.SUCCESS
        return Status.FAILED


class Timeout(Step):
    """Abort the wrapped step if it exceeds `seconds` (cooperative)."""
    def __init__(self, seconds, child):
        super().__init__(); self.seconds = seconds; self.add_child(child)

    def run(self):
        child = self.children[0]
        timer = threading.Timer(self.seconds, child.abort)
        timer.start()
        try:
            return child.execute(self.context, self._node_id)
        finally:
            timer.cancel()
```

**Sweep handling:** `Repeat(10, Sweep(voltages))` is ten passes; `Sweep(voltages, dwell=long)` is one slow pass. Both produce flat measurements tagged with `sweep_index` (from `Repeat`) and `sequence_index` (from the writer). No sweep-specific schema or branching — exactly the database document's "acquisition structure is metadata" principle, enforced at the framework level.

### A Periodic Leaf (Integrate)

```python
class Integrate(Step):
    """Integrate counts for ~int_time, sampling every sample_dt seconds.
    Each sample optionally emitted as a measurement_detail row."""
    determinate = True   # progress = elapsed / int_time

    def __init__(self, int_time, sample_dt=0.5, emit_details=False):
        super().__init__()
        self.int_time = int_time
        self.sample_dt = sample_dt
        self.emit_details = emit_details

    def on_enter(self):
        self._t0 = time.time()
        self._counts = 0
        self._details = []

    def run(self):
        while (time.time() - self._t0) < self.int_time:
            if self.aborted:
                return Status.ABORTED
            self._tick()
            elapsed = time.time() - self._t0
            # SAME loop that enables abort also drives the progress bar
            self.report_progress(min(elapsed / self.int_time, 1.0),
                                  detail=f"{self._counts} counts")
            if not self.sleep(self.sample_dt):   # aborted mid-sleep
                return Status.ABORTED
        delta = time.time() - self._t0
        self.context.data_bus.emit(MeasurementCompleted(
            sweep_index=self.context.sweep_index,
            temperature=self.context.read_temperature(),
            data={"counts": self._counts,
                  "delta_time": delta,
                  "int_time": self.int_time},
            metadata=self.context.snapshot_parameters(),
            details=self._details if self.emit_details else None,
        ))
        return Status.SUCCESS

    def _tick(self):
        new = self.context.read_counts()         # hardware read
        self._counts += new
        if self.emit_details:
            self._details.append({
                "detail_type": "time_window",
                "bin_index": len(self._details),
                "bin_value": time.time() - self._t0,
                "value": new,
            })


class Wait(Step):
    """Interruptible wait. The abort-polling loop also drives progress."""
    determinate = True

    def __init__(self, seconds, step_dt=0.25):
        super().__init__(); self.seconds = seconds; self.step_dt = step_dt

    def on_enter(self):
        self._t0 = time.time()

    def run(self):
        while (elapsed := time.time() - self._t0) < self.seconds:
            if self.aborted:
                return Status.ABORTED
            self.report_progress(elapsed / self.seconds)
            if not self.sleep(min(self.step_dt, self.seconds - elapsed)):
                return Status.ABORTED
        self.report_progress(1.0)
        return Status.SUCCESS
```

Note `Wait` and `Integrate` both demonstrate the central payoff: the loop that
exists for *abortability* is the same loop that emits *progress*. Owning the
thread buys both from one mechanism — neither was possible when an external loop
called a one-shot `evaluate()`.

## The Data Message Bus (Replacing ConcurrentAction for I/O)

Data aggregation and plotting are **removed from the control tree**. The tree manages control flow only. Steps *emit* typed messages to a bus; subscribers (database sink, plotter, logger) consume them. This is the seam to the database document and the Qt-thread boundary.

```python
import queue


class Message: ...

class RunStarted(Message):
    def __init__(self, device_id, run_type, operator, description, config):
        self.device_id=device_id; self.run_type=run_type
        self.operator=operator; self.description=description; self.config=config

class MeasurementCompleted(Message):
    def __init__(self, sweep_index, temperature, data, metadata, details=None):
        self.sweep_index=sweep_index; self.temperature=temperature
        self.data=data; self.metadata=metadata; self.details=details

class RunEnded(Message): ...
class StepFailed(Message):
    def __init__(self, step_name, info): self.step_name=step_name; self.info=info


class MessageBus:
    """Thread-safe fan-out. Worker thread emits; subscribers drain on their
    own thread (the Qt subscriber drains on the GUI thread via a QTimer)."""
    def __init__(self):
        self._subscribers = []          # list of (msg_types, callback)

    def subscribe(self, msg_types, callback):
        self._subscribers.append((tuple(msg_types), callback))

    def emit(self, message):
        for types, cb in self._subscribers:
            if isinstance(message, types):
                cb(message)              # subscriber decides threading policy
```

**Defined data message contract** (the only thing the database document depends on):
`RunStarted`, `MeasurementCompleted`, `RunEnded`, `StepFailed`. Fixed field names. The database `DatabaseSink` subscribes to `RunStarted`/`MeasurementCompleted`/`RunEnded`; the plotter subscribes to `MeasurementCompleted`; a logger subscribes to everything. Progress is **not** on this bus — see the next section.

**Qt boundary:** the worker thread calls `bus.emit(...)`. The Qt subscriber's callback does not touch widgets directly — it pushes the message onto a `queue.Queue`; a `QTimer` on the GUI thread drains that queue and updates plots. The framework has zero `import PyQt5`.

## Status & Progress (A Separate Channel)

Progress is a fundamentally different kind of signal than data, and conflating them is a design error. They differ on every axis that matters:

| Axis | Data (`MeasurementCompleted`) | Progress (`StepProgress`) |
|---|---|---|
| Durability | Must never be lost — it is the scientific record, committed to the DB | Ephemeral — a missed update is superseded 0.25 s later |
| Rate | Rare (one per integration) | High-frequency, bursty (every tick of every active step) |
| Loss policy | Lossless, exactly-once | Lossy-coalescing is *correct* (keep latest per node) |
| Consumers | DB sink, plotter | Progress UI only |

Putting progress on the data bus would either burden progress with durability it doesn't need, or risk the progress firehose delaying the durable data path, or let a database sink accidentally subscribe to progress. The separation must be **structural**, not a convention.

**Decision: a second, independent bus instance of the same `MessageBus` class.** No new mechanism — `data_bus` and `status_bus` are two instances with disjoint subscriber sets. Same code; isolated traffic; the isolation is enforced by them being different objects, so a sink physically cannot cross channels.

### The progress protocol — three messages, frozen

```python
class StatusMessage: ...

class StepBegan(StatusMessage):
    def __init__(self, node_id, parent_id, label, determinate):
        self.node_id = node_id          # path tuple, e.g. ("root","Repeat","Sweep")
        self.parent_id = parent_id      # None for root
        self.label = label              # "Integrate", "Repeat"
        self.determinate = determinate  # False => indeterminate spinner

class StepProgress(StatusMessage):
    def __init__(self, node_id, fraction, detail=None):
        self.node_id = node_id
        self.fraction = fraction        # 0.0–1.0; ignored if indeterminate
        self.detail = detail            # optional: "4823 counts", "2.13 K"

class StepEnded(StatusMessage):
    def __init__(self, node_id, status):
        self.node_id = node_id
        self.status = status            # Status.SUCCESS / FAILED / ABORTED
```

That is the entire interface. Keep it frozen at three messages — logging, instrument readouts, and warnings are *yet another* concern; if wanted, argue for a third channel rather than overloading progress.

### Tiered progress falls out of the tree for free

The framework is already a tree of nested steps with a clean lifecycle, so the progress *hierarchy already exists* — it is not designed, only reflected:

- **`StepBegan` / `StepEnded` are fully automatic for every step**, emitted by the base `Step.execute()` wrapper (shown earlier). `on_enter` → bar appears; `on_exit` → bar completes and disappears. Zero per-step code.
- **The `node_id` is the path tuple**, built as the tree executes (`child.execute(self.context, self._node_id)`). The UI reconstructs nesting from `parent_id` alone — it never needs to know what a step *is*. Tiers correspond exactly to tree depth.
- **Fractional progress is opt-in**, only in steps where a fraction is meaningful: `Sequence` (children done / total), `Repeat` (passes / n), `Integrate` (elapsed / int_time), `Wait` (elapsed / total). Each is one `self.report_progress(...)` line. Steps with no meaningful fraction (`SetVoltage`) emit `StepBegan(determinate=False)` and the UI shows a brief indeterminate tick.

The genuinely valuable part — the *hierarchy* — costs nothing because it *is* the step tree. A composite is meaningfully "30% done" just from being on pass 3, independent of what its children do.

### UI is a generic tree renderer

The progress UI subscribes to `status_bus` and is pure plumbing: a dict `node_id → bar`, nest by `parent_id`, set width from `fraction`, remove on `StepEnded`. It contains no knowledge of physics, instruments, or step types. The exact widget styling is left open; the protocol is the contract.

### Qt coalescing policy (the concrete payoff of separation)

The Qt status subscriber drains its queue on the GUI thread like the data subscriber — but with one deliberate difference: under backlog it **coalesces**, keeping only the latest `StepProgress` per `node_id` and discarding superseded ones. This lossy policy is *correct* for progress and would be *catastrophic* for data — which is the final, concrete reason the two must not share a channel.

```python
def drain_status_queue(q, bars):
    latest = {}                                  # node_id -> last StepProgress
    while not q.empty():
        msg = q.get_nowait()
        if isinstance(msg, StepProgress):
            latest[msg.node_id] = msg            # coalesce: keep only newest
        else:
            apply_status(msg, bars)              # Began/Ended applied in order
    for msg in latest.values():
        apply_status(msg, bars)
```

## Run Context

A `RunContext` is injected into every step's `execute()`. It carries the shared parameter state (what the database calls `metadata`), the current `sweep_index`, instrument handles, and **both buses**.

```python
class RunContext:
    def __init__(self, data_bus, status_bus, instruments):
        self.data_bus = data_bus       # durable: measurements -> DB, plotter
        self.status_bus = status_bus   # ephemeral: progress -> UI only
        self._instruments = instruments
        self._parameters = {}        # bias_current, trigger_level, thermal_power...
        self.sweep_index = None      # set by Repeat

    def set_parameter(self, name, value):
        self._parameters[name] = value     # SetVoltage etc. call this

    def snapshot_parameters(self):
        return dict(self._parameters)      # frozen copy stored as metadata

    def read_counts(self):  return self._instruments.timetagger.read()
    def read_temperature(self): return self._instruments.cryostat.temperature()
```

Parameter-setting steps (`SetVoltage`, `SetBias`, `SetThermalPower`) call `context.set_parameter(...)` in their `run()`. `Integrate` calls `snapshot_parameters()` at completion, so each emitted measurement carries exactly the conditions in force when it finished — the mechanism behind the database document's flat-metadata model.

## Worker Thread Driver

```python
import threading

class TaskRunner:
    """Owns the worker thread. Runs a Step tree to completion off the GUI
    thread, emitting data on data_bus and progress on status_bus."""
    def __init__(self, data_bus, status_bus, instruments):
        self.data_bus = data_bus
        self.status_bus = status_bus
        self.context = RunContext(data_bus, status_bus, instruments)
        self._thread = None
        self._root = None

    def start(self, root_step, run_started: RunStarted):
        self._root = root_step
        self._thread = threading.Thread(
            target=self._main, args=(run_started,), daemon=True)
        self._thread.start()

    def _main(self, run_started):
        self.data_bus.emit(run_started)
        try:
            status = self._root.execute(self.context)   # parent_id defaults None
            if status is Status.FAILED:
                self.data_bus.emit(StepFailed(self._root.name, "root failed"))
        finally:
            self.data_bus.emit(RunEnded())

    def abort(self):
        if self._root: self._root.abort()
```

## Example: A PCR Curve, Two Acquisition Strategies

```python
# Single long sweep (heat drift visible in later points):
single = Sequence().add_child(
    Sweep(voltages=biases, dwell_step=Integrate(int_time=10.0)))

# Ten averaged passes (heat smeared across all points):
averaged = Repeat(10,
    Sweep(voltages=biases, dwell_step=Integrate(int_time=1.0)))

runner.start(averaged, RunStarted(
    device_id=7, run_type=RunType.PCR_CURVE,
    operator="me", description="PCR W2026-03-A7", config={...}))
```

Both emit flat `MeasurementCompleted` messages. `averaged` tags each with `sweep_index` 0–9; the database stores them as flat rows; analysis reconstructs per-sweep or averaged curves by query (see database document, *Acquisition-Position Metadata*). The two strategies differ only in composition — no special code, no schema difference.

## Recommended Build Order

1. `Status`, `Step` (with base `execute()` emitting `StepBegan`/`StepEnded`), `Sequence`, interruptible `sleep`/`abort`, `RunContext` (both buses).
2. `MessageBus`; instantiate `data_bus` and `status_bus`; a stdout logger on each to verify both contracts.
3. `Integrate` with internal `_tick()` sampling + `report_progress`; verify data on `data_bus`, progress on `status_bus`.
4. `DatabaseSink` subscriber (from the database document) on `data_bus` — persistence working end to end.
5. Decorators: `Repeat` (sweeps, emits pass progress), `Retry`, `Timeout`.
6. `Parallel` (cooperative) only when a real concurrent need appears.
7. Qt subscribers: data queue + `QTimer` drain for plotting; **separate** status queue + `QTimer` drain with the coalescing policy for tiered progress bars. Both fully decoupled; framework has zero `import PyQt5`.

## Summary

A behavior-tree-shaped framework that owns its own worker thread instead of borrowing an event loop. Public contract is a blocking `run()` with explicit `on_enter`/`on_exit` lifecycle, first-class failure and abort, and interruptible sleep. Periodic ticking survives only inside long-running leaves (`Integrate`, `Wait`) and `Parallel`, where it is genuinely needed — not as a universal contract; the abort-polling loop doubles as the progress-emission loop, a benefit only possible because the framework owns its time. Sweeps and retries are decorators, not special cases. Output flows on **two independent channels**: a durable `data_bus` (measurements → database, plotter) and an ephemeral `status_bus` (a frozen three-message progress protocol → tiered progress UI). The separation is structural — two bus instances — so durability and lossy-coalescing policies cannot cross. Tiered progress is essentially free because the progress hierarchy *is* the step tree, reflected via path-tuple `node_id`s emitted automatically by the base lifecycle. The framework's job is control flow and emission; the database, the plotter, and the progress UI are all just subscribers, keeping the framework ignorant of storage, PyQt, and presentation alike.
