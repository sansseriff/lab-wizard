"""Composable procedures, as lab_wizard stores and generates them.

The step schemas, definitions and palette catalog are lab_procedure's
(:mod:`lab_procedure.schema`, :mod:`lab_procedure.definition`); lab_wizard's
instrument steps and their schemas are in
:mod:`lab_wizard.lib.task_adapters.instrument_steps`. Here: plots and derived
columns on a definition, storage under ``config/procedures/``, and turning a
definition into the Python a generated project runs — nothing here interprets
YAML at run time. See ``plans/procedure_plan.md``.
"""

# A definition can name lab_wizard's instrument steps once their module is imported.
import lab_wizard.lib.task_adapters.instrument_steps  # noqa: E402, F401
