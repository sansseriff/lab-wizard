# Step Parameter & Metadata System: Open Questions

> Companion to **Task Tree Framework** (acquisition) and **Lab Measurement
> Database** (storage). This document does *not* propose a schema. It enumerates
> the decisions a YAML-tree-driven parameter system for Steps must resolve
> before a design is attempted, and flags where Steps differ fundamentally from
> the existing instrument-tree system so those differences are decided
> deliberately rather than by accident.

## Why This Is Harder Than the Instrument Tree

The existing YAML system describes instruments that are children of instruments (a voltage source under a mainframe). That tree is **structural and static**: the hardware exists, the tree mirrors physical containment, and a node's parameters configure a thing that is already there. Each node maps 1:1 to a real device.

A Step tree is **behavioral and generative**. A parameter on a Step does not just configure an existing object — it can determine *how many objects exist*, *what order they run in*, and *what data they emit*. `Integrate(int_time=3, sample_dt=0.1)` wrapped in `Repeat(n=10)` is not "one configured Integrate"; it is a directive to instantiate ten independent Integrate executions. The parameters are not only configuration; some of them are **construction instructions for the tree itself**. This is the central reason the instrument-tree model cannot be extended naively, and most of the open questions below are consequences of it.

## 1. The Configuration-vs-Construction Boundary

Some Step parameters configure behavior (`int_time=3`, `sample_dt=0.1`). Others determine tree shape (`Repeat n=10`, `Sweep values=[...]` whose length sets how many children exist). A third, subtler class does both: `sample_dt` configures Integrate *and* implies how many progress/detail emissions occur.

**Open questions:**
- Does the YAML distinguish "parameters that tune a step" from "parameters that multiply or shape steps," or are they uniform and the Step class decides at build time?
- Is tree shape fully determined by the YAML before execution (static expansion), or can it depend on runtime values (a sweep whose points come from a prior measurement — the old `DependentAction` case)? Static is far simpler to reason about, validate, and serialize; runtime-dependent shape is sometimes physically necessary.
- If both are needed, is there a clean split between a "statically expandable" subset and an "execution-time" subset, and where is that line drawn?

## 2. Duplication Semantics (The Core Problem You Named)

"Make 10 separate Integrate steps, each integrating 3 s, each emitting progress every 0.1 s." The `10`, the `3`, and the `0.1` are all metadata, but they play different roles: `10` is a *multiplicity*, `3` and `0.1` are *per-instance configuration*. The system must define how a single YAML declaration expands into N independent instances.

**Open questions:**
- **Identity of duplicates.** When a Step is duplicated 10×, what distinguishes instance 4 from instance 7? The framework already needs a `node_id` path tuple for progress nesting and a `sweep_index` for the database. Does the parameter system *own* the assignment of these indices, or merely declare multiplicity and let the framework assign identity at expansion? (Recommend the latter as a question to settle early — it keeps the YAML free of execution bookkeeping.)
- **Per-instance parameter variation.** Pure duplication (10 identical Integrates) is the easy case. The real case is duplication *with* a varying bound parameter (sweep bias over 10 values, integrate at each). How does YAML express "duplicate this subtree once per value in this list, binding the value into a named parameter of a specific descendant"? This is the `ForEach` construct from the framework document, expressed declaratively.
- **Which node receives the swept value?** In `Sweep(bias, [...], body=Sequence[SetBias, Integrate])`, the swept value must reach `SetBias`, not `Integrate`. In a deep subtree the target may be several levels down. How does a parent's sweep declaration address a parameter on a non-adjacent descendant without the YAML becoming a brittle web of cross-references?
- **State independence of duplicates.** Each duplicate needs its own execution state (`_t0`, `_counts`). Is independence guaranteed structurally (the expander deep-copies the subtree per instance) or behaviorally (instances share a template and reset in `on_enter`)? This was an open decision in the framework document; the parameter system forces it to be answered because *the YAML is where multiplicity is declared*.

## 3. Parameter Scope and Inheritance

The instrument tree has simple inheritance: a child instrument inherits or overrides settings from its mainframe. Step trees have a harder version because of the parameter-snapshot model in the database design: a measurement's `metadata` is "all parameters in force when it finished." That set is assembled by *execution order*, not by tree containment.

**Open questions:**
- **Two distinct inheritance axes.** (a) *Configuration inheritance*: does an `Integrate` inherit a default `sample_dt` from an ancestor that sets a lab-wide default? (b) *Parameter-context inheritance*: the `bias_current` set by a `SetBias` step is "in force" for every subsequent `Integrate` until changed — this is **temporal/dynamic**, not structural, and is exactly what the database `snapshot_parameters()` captures. The YAML system must not conflate these. Configuration inheritance is a tree property; parameter context is an execution-trace property. Does the YAML even attempt to express the second, or is it purely a runtime concern owned by `RunContext`?
- **Override precedence.** If a default exists at the root, an override at a `Sweep`, and another at the swept body, what wins, and is precedence positional (nearest ancestor) or explicit (priority field)? The instrument tree likely already has an answer; the question is whether Steps can reuse it or need different rules because of duplication (does an override on a duplicated subtree apply to all instances or can it vary per instance?).
- **Where does a swept/bound value live for the database?** When `Sweep` binds `bias=12.5` for instance 3, that value must end up in that measurement's `metadata`. Is the YAML parameter system responsible for ensuring bound values flow into `RunContext.set_parameter`, or is that purely the framework's job and the YAML only declares the sweep? The seam between "declared parameter" and "recorded metadata" needs an explicit owner.

## 4. Identity, Naming, and Addressing

To duplicate a subtree, override a deep parameter, or route a swept value, the YAML must be able to *name* nodes. The instrument tree gets this for free (instruments have physical identities). Steps do not inherently have stable names, and duplication makes naming ambiguous (which of the 10 `Integrate`s?).

**Open questions:**
- What is a Step's stable identifier in the YAML — positional path, explicit user-assigned name, type-plus-index? The framework already derives a runtime `node_id` path tuple; should the YAML identifier be the *source* of that, so declaration-time and execution-time identity coincide?
- How are duplicated instances addressed after expansion (e.g., to attach per-instance overrides or to correlate with database `sequence_index`)? Is there a deterministic, documented index assignment?
- Reusability: can a subtree be defined once and referenced/instantiated in multiple places (a YAML anchor / include / macro)? If so, identity must distinguish "the template" from "this instantiation," which interacts with every duplication question above.

## 5. Validation and Failure Mode

The instrument tree can be validated against present hardware. A Step tree's correctness is partly about *behavior* and *expansion*, which is harder to check statically.

**Open questions:**
- Is the YAML validated before a run starts (fail fast, before any instrument moves), and how much can be validated without executing — required parameters present, types correct, swept-value targets resolvable, multiplicities non-negative? Runtime-dependent shape (Q1) limits how much is statically checkable.
- What is the failure behavior of a malformed parameter tree: refuse to start the run, or start and fail at the offending step? For a lab where a bad run wastes cryostat time, fail-fast-before-cooldown is likely the requirement — but that constrains how dynamic the system can be.
- Are parameter *ranges/safety limits* (max bias before device damage) part of this system or a separate instrument-safety layer? If part of it, the system now has a safety-critical role and its validation guarantees matter much more.

## 6. Serialization and the Reproducibility Record

The database design stores a `config` JSON on each `run` — explicitly intended to hold "the serialized task tree." This couples the parameter system to the storage contract.

**Open questions:**
- Is the stored `config` the *authored YAML* (compact, human-meaning, but not the literal thing executed), the *fully expanded tree* (the literal 10 Integrate instances, large but exact), or both? Reproducibility argues for the expanded form; readability and diffing argue for the authored form. This is a direct, concrete decision the database document is waiting on.
- Must a stored config be re-runnable later, and if so does it pin instrument identities, calibration files, and software version alongside the parameter tree? The instrument-tree YAML may already encode some of this; the boundary needs definition.
- If the YAML supports includes/anchors/macros (Q4), does serialization store the resolved tree (self-contained, reproducible) or the unresolved references (depends on external files that may change)?

## 7. Relationship to the Existing Instrument Tree

A Step like `SetBias` *acts on* an instrument that lives in the existing instrument YAML tree. There are now two trees that must refer to each other.

**Open questions:**
- How does a Step parameter reference an instrument node in the other tree (by the instrument tree's existing identifier, presumably) without hard-coupling the two YAML schemas?
- Are the two trees separate documents that cross-reference, one nested in the other, or a single unified tree with both instrument and step nodes? Unification is conceptually tidy but merges a static structural tree with a generative behavioral one — likely the wrong move, but it should be rejected explicitly with reasons, not by omission.
- Does changing an instrument's identity in the instrument tree break Step references, and is there a defined indirection (logical role names → physical instruments) to insulate Step definitions from hardware reconfiguration?

## 8. Authoring Ergonomics vs Expressive Power

The reason to use YAML at all is that humans author and read it. Every capability above (runtime-dependent shape, deep parameter addressing, per-instance overrides, macros) increases power and decreases legibility. The recurring design tension from the other two documents — resist generality that is paid for on every use — applies here with full force.

**Open questions:**
- What is the *minimum* expressive set that covers the lab's actual measurement patterns (sweeps, repeats, nested sweeps, a configurable per-point body)? Can the first version deliberately exclude runtime-dependent shape and macros, matching the "start minimal, promote when proven" principle the database document adopted for columns?
- Where does logic belong when YAML becomes awkward — is there a defined escape hatch (a named Python-defined subtree referenced from YAML) so the YAML stays declarative and complex control flow stays in code, rather than YAML slowly accreting a programming language?
- Who is the author — the person running the experiment each day, or a developer defining reusable measurement types once? The answer changes how much power vs guardrails the system should expose.

## Summary of Key Decisions to Make First

The decisions that gate the rest, in rough priority:

1. **Static vs runtime-dependent tree shape** (Q1) — constrains validation, serialization, and how dynamic everything else can be. Decide this first; it is load-bearing.
2. **Duplication: template-shared vs deep-copied instances** (Q2) — the framework left this open; the parameter system forces it because multiplicity is declared in YAML.
3. **Owner of node identity/indexing** (Q2, Q4) — does YAML declare multiplicity only, with the framework assigning `node_id`/`sweep_index`? Keeps execution bookkeeping out of the declarative layer.
4. **Configuration inheritance vs parameter-context (temporal) state** (Q3) — these are different axes and must not be conflated; decide whether YAML expresses only the first.
5. **Stored config form: authored vs expanded** (Q6) — a concrete dependency the database `config` field is already waiting on.
6. **Two-tree relationship** (Q7) — cross-reference vs unify; likely cross-reference, but decide explicitly.
7. **Minimum viable expressiveness** (Q8) — fix the smallest covering feature set before any schema work, consistent with the minimal-first philosophy of the other two documents.

None of these requires inventing the schema now; each must be answered before a schema can be designed without trapping the lab in an over-general or under-powered system.
