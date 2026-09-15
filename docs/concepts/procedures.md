---
icon: lucide/workflow
---

# Procedures

A **procedure** is a reusable measurement recipe written as data: which kinds of
instrument it needs, which parameters it takes, and a tree of steps. It lives in
`config/procedures/<name>.yml`, and generating a project from it produces
ordinary Python — the same kind of project a hand-written measurement produces.
Nothing reads the YAML at run time.

Hand-written measurements under `lib/measurements/` keep working unchanged. A
procedure is a second way to get the same result without writing Python.

## A definition

```yaml
name: mcr_curve
description: Count rate against optical attenuation
roles:
  attenuator: {behavior: Attenuator}
  counter: {behavior: Counter}
params:
  attenuation:
    sweep: {type: sweep, default: {mode: linear, start: 0.0, stop: 30.0, step: 1.0}}
    settle_s: {type: float, default: 0.2, unit: s}
  readout:
    gate_time_s: {type: float, default: 1.0, unit: s}
body:
  type: safe_guard
  instrument: {role: attenuator}
  body:
    type: sweep
    parameter: attenuation_db
    values: {param: attenuation.sweep}
    body:
      type: sequence
      children:
        - {type: set_attenuation, attenuator: {role: attenuator}, attenuation_db: {swept: attenuation_db}}
        - {type: wait, seconds: {param: attenuation.settle_s}}
        - {type: count, counter: {role: counter}, gate_time: {param: readout.gate_time_s}}
```

**Roles** are the procedure's signature. They name behaviors (`VSource`,
`VSense`, `Counter`, `Attenuator`), not instruments; any configured instrument
with that behavior can fill the role when a project is generated.

**Params** are groups and typed leaves: `float`, `int`, `bool`, `str`, or
`sweep`. A sweep is linear (`start`, `stop`, `step`) or explicit (`values`),
with no unit in the field names — the same sweep drives volts or decibels, so
the unit goes on the param. (The older `start_V`-style names still load.)
Params become the project's `measurement.params` and a pydantic model in the
generated setup file.

**The body** is the step tree. A step's fields hold one of:

| Form | Meaning |
|---|---|
| `0.05`, `true`, `[0.0, 0.1]` | a literal |
| `{param: readout.gate_time_s}` | a value from the params |
| `{swept: attenuation_db}` | the value an enclosing `sweep` is at |
| `{role: counter}` | the instrument bound to a role |
| a step, or a list of steps | children |

## Steps

| Step | Does |
|---|---|
| `sequence` | children in order; stops at the first that does not succeed |
| `sweep` | its body once per value, binding `parameter` |
| `repeat`, `wait` | the obvious |
| `with_parameter` | its body with `parameter` set to `value`, so its rows carry it — `phase: background` |
| `retry` | its child until it succeeds, up to `max_attempts`; a raised error is retried too |
| `if` | `then` if `condition` succeeds, else `otherwise` (or nothing) |
| `selector` | children in order until one succeeds |
| `invert` | succeeds when its child fails |
| `value_above`, `value_below` | succeed when the latest recorded `field` is above / below `threshold` |
| `set_voltage`, `turn_on`, `return_to_zero_and_off` | drive a `VSource` |
| `source_guard` | run a body with a source on; return it to 0 V and off afterwards, always |
| `safe_guard` | run a body; put an instrument in its declared safe state afterwards, always |
| `with_settings` | run a body with settings overridden, restoring them afterwards |
| `set_threshold`, `count` | drive a `Counter`; `count` records `counts`, `int_time`, `count_rate` |
| `read_voltage` | read a `VSense`; records the reading under `field` |
| `set_attenuation`, `open_shutter`, `close_shutter` | drive an `Attenuator` |

Branching needs no expression language: every step succeeds, fails, or is
aborted, and a condition is just a step that fails. `sequence` with a
`value_below` in it stops the run when a count rate climbs too high; `retry`
around a `count` recovers from a timeout.

Every recorded row is flat: it carries the swept values in force as well as the
reading, so a nested sweep produces more rows, never a nested structure.

## The Procedures section

The wizard's **Procedures** section is the composer for all of this: no YAML
needs to be written by hand. It lists every procedure — built into lab_wizard or
saved in this workspace — and opens one for editing.

The editor has three parts, and a panel that says whether what you have can be
generated:

* **Roles** — a name and a behavior each, which is the procedure's signature.
  Beside each is how many of this workspace's instruments can fill it, so a role
  that narrows where the procedure can run is visible as you declare it.
* **Params** — groups and typed leaves, with a default, a unit, and what the
  param means; the description becomes a comment in every project's YAML.
* **Steps** — the tree. *Add step* offers the operations grouped by role, so
  picking one under `counter` binds it to `counter`; a step whose behavior no
  role has yet is offered too, and choosing it declares the role. *Wrap* puts a
  step inside a new one (a sweep, a guard, a retry) without rebuilding it.

A value is a literal until you press **Make param** on it, which declares a
param with what you typed as its default and points the step at it. That is the
whole difference between something frozen into the procedure and something every
project and preset can set.

Everything is checked after each edit, against the same rules used at save and
generation time. Each problem is marked on the step it is about, and the sidebar
lists them all; clicking one scrolls to it. Nothing that does not check can be
saved. The **Python** tab shows the module the definition generates, the
**YAML** tab the file it is saved as — editable, if hand-editing is quicker —
and the **Presets** tab manages a saved procedure's presets.

Saving a built-in under its own name writes this workspace's copy, which takes
precedence; deleting that copy brings the built-in back.

## Checking

A definition is checked before it is saved or generated, and every problem is
reported at once: a role or param that is not declared, a role filled by the
wrong behavior (`count` given a `VSource`), a swept value used outside its
sweep, a sweep param used as a single number, or a condition on a field no step
records.

## Built-in procedures

Procedures that ship with lab_wizard live in
`lab_wizard/lib/procedures/library/`. A workspace procedure of the same name
takes precedence, so a lab adapts a built-in by saving its own copy; deleting
that copy brings the built-in back. The first built-in is **`mcr_curve`**: count
rate against optical attenuation at a fixed bias, with a background count taken
through a closed shutter, rebuilt from the old `mcrCurve.py`.

## Presets

`config/measurements/<measurement>/<preset>.yml` holds a named set of params —
"the lab's standard sweep" — for a procedure or a hand-written measurement.
Generating a project copies the preset's values into the project; editing the
preset afterwards changes no existing project.

## Generated code, and editing it

A project generated from a procedure contains `<name>.py`, holding
`build_<name>_procedure()` and a `<Name>Measurement` class, and a setup file with
the params models and the usual run lifecycle. The step tree sits between
`# wizard:procedure:start` and `# wizard:procedure:end`. Regenerating from a
changed definition replaces only that block, so anything you add around it
survives.

Adding a step type is one small class in `lib/procedures/steps/`: a pydantic
model whose field names match the runtime step's constructor. The generator
needs no change.
