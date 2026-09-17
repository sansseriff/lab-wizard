---
icon: lucide/git-branch
---

# Procedures

The **Procedures** section is where a measurement's choreography is written —
the roles it needs, the parameters it takes, and the tree of steps it runs — as
a definition rather than as Python. What a procedure *is* and how it is stored
is [Procedures](../concepts/procedures.md); this page is the section itself.

## The library

`/procedures` lists every procedure available here: the ones built into
lab_wizard and the ones saved in this workspace's `config/procedures/`. Each row
shows its roles, the data columns it records, its presets, and whether it
currently checks.

- **New procedure** starts from an empty sequence.
- **Duplicate** opens a copy under a new name — the usual way to adapt a
  built-in without losing the original.
- **Create measurement** goes straight to binding instruments for it.
- **Delete** removes this workspace's copy. Where that copy overrides a built-in
  the button reads **Revert**, and the built-in comes back.

Saving a built-in under its own name writes a workspace copy that takes
precedence here; the packaged one is never modified.

## The editor

Four parts, plus a panel that says whether what you have can be generated.

### Roles

A role is a name and a behavior — `counter: Counter` — and together they are the
procedure's signature. A role names *a kind of instrument*, never a particular
one, which is what lets the same procedure run on any rack.

Beside each role the editor shows how many of this workspace's instruments could
fill it. A role nothing here can fill is legal — a server elsewhere may have one
— but it is worth seeing as you declare it, because it is exactly the
portability cost of asking for something specific.

Renaming a role rewrites every step that referenced it.

### Parameters

Groups and typed leaves (`float`, `int`, `bool`, `str`, `sweep`), each with a
default, a unit, and a description. The description becomes a comment in every
project's YAML, so it is worth writing.

These are what a project can tune without regenerating, and what a
[preset](#presets) sets.

### Steps

The tree, top to bottom, nested steps inside their parent.

**Add step** offers the operations grouped by role: under `counter` you see what
a counter can do, and choosing one binds it to that role. A step whose behavior
no role has yet is offered too, under the behavior it needs — picking it
declares the role for you. That is how most roles get created in practice.
Steps that need no instrument are grouped under structure and flow.

**Wrap** puts the selected step inside a new one — a sweep, a guard, a retry —
without rebuilding it. **Unwrap** is the inverse where a container holds a
single step.

### Values: fixed, parameter, or swept

Every value a step takes is one of three things, chosen per field:

| Mode | Means |
|---|---|
| **Fixed value** | frozen into the procedure |
| **Parameter** | read from the project's YAML, so every project can set it |
| **Current sweep value** | the value an enclosing sweep is at |

**Make parameter** turns the literal you typed into a declared parameter with
that value as its default, and points the step at it. That one button is the
whole difference between something baked into the procedure and something every
project and preset can change.

## Checking

Everything is checked after each edit, by the same rules used when saving and
when generating code — an undeclared role or parameter, a role filled by the
wrong behavior, a swept value used outside its sweep, a condition on a column no
step records. Each problem is marked on the step it concerns, and the sidebar
lists them all; clicking one scrolls to it.

**Nothing that does not check can be saved**, so a saved procedure always
generates.

## YAML and Python tabs

- **YAML** is the file as it is stored, editable if hand-editing is quicker.
  *Apply* loads it back into the composer.
- **Python** is the module a project would get. The step tree sits between
  `# wizard:procedure:start` and `# wizard:procedure:end`; regenerating a
  project replaces only that block, so edits you make around it survive.

Nothing interprets the YAML at run time — a project runs the generated Python.
The Python tab is there so that is never a mystery.

## Presets

A preset is a named set of parameter values — "the lab's standard sweep" —
stored in `config/measurements/<procedure>/<preset>.yml`. Choosing one while
[creating a measurement](measurements.md) copies its values into the new
project; editing the preset afterwards changes no existing project.

Presets are validated against the procedure's **saved** parameters, so the
Presets tab waits for unsaved parameter changes to be saved.

## Where a procedure goes next

A saved procedure appears in [Create measurement](measurements.md) beside the
hand-written measurements, with its roles to bind and its presets to choose
from. From there it is an ordinary project: generated Python, an editable YAML,
and a run.
