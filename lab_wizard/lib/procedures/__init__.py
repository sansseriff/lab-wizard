"""Composable procedures: step schemas, definitions, storage, and code generation.

A *procedure* is a reusable, instrument-generic recipe — roles typed by behavior,
a params model, and a tree of steps. It is stored as YAML under
``config/procedures/`` and turned into ordinary Python in a generated project;
nothing here interprets YAML at run time. See ``plans/procedure_plan.md``.
"""
