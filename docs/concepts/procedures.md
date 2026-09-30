---
icon: lucide/workflow
---

# Procedures

A **procedure** is a reusable measurement recipe written as data: which kinds of
instrument it needs, which parameters it takes, and a tree of steps. It lives in
`config/procedures/<name>.yml`, and generating a project from it produces
ordinary Python — the same kind of project a hand-written measurement produces.
Nothing reads the YAML at run time.

Every measurement that ships with lab_wizard is a procedure. For what a step
tree composed this way cannot say, a lab writes a
[custom measurement](../wizard/measurements.md#custom-measurements): a Python
file in its workspace's `measurements/` folder.

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
`sweep`. A sweep is linear (`start`, `stop`, `step`), explicit (`values`), or
waypoints (`points`, `step`), with no unit in the field names — the same sweep
drives volts or decibels, so the unit goes on the param. (The older
`start_V`-style names still load.)

A **waypoints** sweep walks straight legs between turning points:
`points: [0, 1.4, 0, -1.4, 0]` with `step: 0.005` is an IV loop — up, back,
down, back — and is the built-in `iv_curve`'s default. Each turning point is
visited once, and every point records the leg it was taken on
(`bias_voltage_leg`: 0, 1, 2, 3), so a hysteretic curve's branches can be
filtered, or drawn one line per leg (`series: bias_voltage_leg`, as the
`iv_curve`'s *IV by leg* plot does).
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
| `repeat` | its body `count` times, binding `parameter` (default `repeat`) to 0, 1, 2 … so each repetition is its own row |
| `wait` | waits, abortably |
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
| `set_threshold`, `count` | drive a `Counter`; `count` records `counts`, `int_time` (s), `count_rate` (Hz) |
| `read_voltage` | read a `VSense`; records the reading under `field`, in V |
| `set_attenuation`, `open_shutter`, `close_shutter` | drive an `Attenuator` |
| `laser_on`, `laser_off`, `set_laser_power` | drive a `Laser` |

`set_attenuation`, `set_threshold` and `set_laser_power` take an optional
`record:` naming a column. The value the instrument actually reached is
recorded there, beside the value that was asked for. Hardware quantizes and
clamps: an attenuator asked for 12.34567 dB reports 12.346. (`set_voltage` has
no `record:`, because a voltage source cannot report back what it holds.)

Branching needs no expression language: every step succeeds, fails, or is
aborted, and a condition is just a step that fails. `sequence` with a
`value_below` in it stops the run when a count rate climbs too high; `retry`
around a `count` recovers from a timeout.

## How readings become rows

A run's data is a table of rows. **A row is everything recorded while the same
parameter values were in force.** The parameters are whatever `sweep`,
`repeat`, `retry` (its `attempt`) and `with_parameter` have bound at the time,
and every row carries them.

Recording the same field twice under the same parameters is an error, since the
two readings would be rows nothing tells apart. Measuring a voltage, waiting,
and measuring it again at one sweep point is written one of two ways:

- **one row, two names** — `voltage_before` and `voltage_after`, when the two
  readings are a pair to compare (a derived `voltage_after - voltage_before`
  is then one column);
- **a row each, labelled** — each reading inside a `with_parameter`
  (`phase: before`, `phase: after`), or a `repeat`, when they are samples of one
  quantity to plot or average together.

So in an MCR curve, the `count` and the `read_voltage` taken at one attenuation
share a row, while the background count, taken before any attenuation is set,
is a row of its own:

```text
seq  phase       attenuation_db  counts  count_rate  device_voltage
0    background                  1       20.0
1    signal      20.0            387     7740.0      0.0
2    signal      10.0            4004    80080.0     0.0
```

A nested sweep produces more rows, never a nested structure, and **the order of
the loops does not change the rows**: sweeping trigger level inside bias, or
bias inside trigger level, records the same rows in a different order. The
order is kept in each row's `seq`, and each row names the steps that recorded
into it, as paths like `sweep[0]/sequence#2/count[2]` (`#2` is the sweep's third
value, `[2]` the third child of its sequence).

A reading placed at an outer loop level is taken fewer times, so it lands on a
row of its own with fewer parameters. A step may not record a field with the
same name as a parameter in force; the row already carries it.

## Plots and derived columns

A definition can say how its runs are usually looked at, and what to compute
from them:

```yaml
derived:
  rate_above_dark: 'count_rate - mean(count_rate, phase == "background")'
plots:
  - name: MCR
    x: attenuation_db
    y: [rate_above_dark]
    where: {phase: signal}
    log_y: true
```

The **first plot** is what the Data page and a live plotter draw for a run;
without any, a run is shown as its first recorded column against its innermost
sweep. Any column can go on any axis. `where` keeps only some rows, while
reductions in `x` and `y` still see the whole run, so the signal above is
subtracted by its own background.

A **derived column** is computed whenever a run is read and never stored, so
fixing an expression fixes every past run. The language is small: column names,
numbers, `+ - * / **`, `abs sqrt exp log log10`, the per-run reductions
`mean min max sum count first last` (each optionally with a condition), and
`param("path")` for a value from the run's own params:

```yaml
derived:
  current: (bias_voltage - sense_voltage) / param("readout.bias_resistance_ohm")
```

That is how `iv_curve` gets its current: the resistance each run used is in
its params, so a run taken with a different resistor is still right.

## Writing one

Procedures are written in the wizard's **Procedures** section — roles,
parameters and the step tree, with every edit checked and the generated Python
visible in a tab. See [Procedures](../wizard/procedures.md) for the composer
itself. Nothing stops you writing the YAML by hand; the composer writes exactly
the same file.

## Checking

A definition is checked before it is saved or generated, and every problem is
reported at once: a role or param that is not declared, a role filled by the
wrong behavior (`count` given a `VSource`), a swept value used outside its
sweep, a sweep param used as a single number, a condition on a field no step
records, a nested sweep that reuses its parent's name (the inner value would
replace the outer one in every row), a reading recorded under a bound
parameter's name, or a plot or derived column that names a column the procedure
never records or a param it does not declare. Two steps recording the same field
in one loop body generate and run, and are reported as a warning.

## Built-in procedures

Procedures that ship with lab_wizard live in
`lab_wizard/lib/procedures/library/`. A workspace procedure of the same name
takes precedence, so a lab adapts a built-in by saving its own copy; deleting
that copy brings the built-in back. There are three:

- **`iv_curve`**: the voltage across the detector against its bias, with the
  current through the bias resistor as a derived column.
- **`pcr_curve`**: photon count rate against bias, with the counter's threshold
  set by the run.
- **`mcr_curve`**: count rate against optical attenuation at a fixed bias, with
  a background count taken through a closed shutter, rebuilt from the old
  `mcrCurve.py`.

`iv_curve` and `pcr_curve` were hand-written Python and kept the same params, so
their presets and project YAML still load.

## Presets

`config/measurements/<measurement>/<preset>.yml` holds a named set of params —
"the lab's standard sweep" — for a procedure.
Generating a project copies the preset's values into the project; editing the
preset afterwards changes no existing project.

## Generated code, and editing it

A project generated from a procedure contains `_measurement/<name>.py`, holding
`build_<name>_procedure()` and a `<Name>Measurement` class, and a setup file with
the params models and the usual run lifecycle. The step tree sits between
`# wizard:procedure:start` and `# wizard:procedure:end`. Regenerating from a
changed definition replaces only that block, so anything you add around it
survives.

Adding a step type is one small class in `lib/procedures/steps/`: a pydantic
model whose field names match the runtime step's constructor. The generator
needs no change.
