"""Record a run from plain Python: a ``for`` loop, and one call per row.

A procedure is a step tree, and a step records with ``context.observe``, which
merges readings into rows by the parameters in force. That is what lets a
composed tree's nested steps share rows without knowing about each other. Code
you write yourself does not need it: it knows where its rows begin and end. So
here a row is exactly one call::

    run.row(sense_voltage=sense.get_voltage())

and the parameters it was taken at are bound around it::

    for bias in biases:
        source.set_voltage(bias)
        with run.at(bias_voltage=bias):
            run.row(sense_voltage=sense.get_voltage())

Rows land in the lab database exactly as a procedure's do, so the Data page,
live plots and saved files treat them the same: one row per call, with
``bias_voltage`` as a column like any sweep's.

Two ways in:

* **A custom measurement** (``lib/custom_measurements.py``) defines
  ``measure(resources, run)`` instead of ``build_procedure``. Its project
  claims, baselines and makes safe its instruments like any other, and the
  wizard runs, stops and plots it.
* **A script or notebook** opens a run itself with :func:`record_run`::

      with record_run("quick_iv", device="A7", plots=[{"x": "bias_voltage", "y": ["sense_voltage"]}]) as run:
          ...

  It is recorded in the workspace's lab database; nothing claims or resets
  your instruments for you.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

from lab_procedure import RunContext, RunEnded, RunStarted, Status, Step, StepBegan, StepEnded

from lab_wizard.lib.data.encoding import jsonable
from lab_wizard.lib.task_adapters.provenance import baseline_snapshot
from lab_wizard.lib.task_adapters.sinks import RunSink

__all__ = ["MeasureStep", "Recording", "RunStopped", "record_run"]


class RunStopped(KeyboardInterrupt):
    """The run was stopped — Stop in the wizard, or Ctrl-C — while it waited.

    A ``KeyboardInterrupt``, so it ends the run as ``aborted`` exactly as a
    Ctrl-C does, and ``except Exception`` in your code does not swallow it.
    """


class Recording:
    """The handle a measurement records its rows through."""

    def __init__(self, context: RunContext, step: Step | None = None) -> None:
        self.context = context
        self._step = step

    @contextmanager
    def at(self, **parameters: Any) -> Iterator[None]:
        """Record every row inside at these parameter values, as a sweep would.

        They nest: ``with run.at(trigger_mV=t):`` around ``with
        run.at(bias_voltage=b):`` gives rows carrying both.
        """
        with ExitStack() as bound:
            for name, value in parameters.items():
                bound.enter_context(self.context.bound_parameter(name, value))
            yield

    def row(self, **values: Any) -> None:
        """Record one row: the parameters in force, and ``values``.

        A value may be a number, text, a bool, or a list (a histogram). A
        value named like a parameter in force is refused, since every row
        carries its parameters already.
        """
        self.context.close_point()  # anything a step left open is its own row
        self.context.observe(values)
        self.context.close_point()

    @property
    def latest(self) -> Mapping[str, Any]:
        """The most recent value recorded under each name, in this run."""
        return self.context.latest

    def sleep(self, seconds: float) -> None:
        """Wait ``seconds``; raises :class:`RunStopped` if the run is stopped meanwhile.

        ``time.sleep`` also works under Ctrl-C and the wizard's Stop; this one
        also notices a stop requested from another thread.
        """
        if self._step is None:
            time.sleep(seconds)
        elif not self._step.sleep(seconds):
            raise RunStopped("the run was stopped")


class MeasureStep(Step):
    """A step that runs ``measure(resources, run)``: a custom measurement written as a loop."""

    def __init__(self, measure: Callable[[Any, Recording], Status | None], resources: Any, name: str | None = None) -> None:
        super().__init__(name=name or getattr(measure, "__name__", None))
        self.measure = measure
        self.resources = resources

    def run(self) -> Status:
        assert self.context is not None
        status = self.measure(self.resources, Recording(self.context, self))
        return status if isinstance(status, Status) else Status.SUCCESS


@contextmanager
def record_run(
    procedure: str,
    *,
    database: str | Path | None = None,
    device: str | None = None,
    operator: str | None = None,
    notes: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    params: Any = None,
    units: Mapping[str, str | None] | None = None,
    plots: list[dict[str, Any]] | None = None,
    resources: Any = None,
    sinks: list[RunSink] | None = None,
) -> Iterator[Recording]:
    """Record one run in the lab database, from a script or a notebook.

    ``database`` defaults to the lab database of the workspace around the
    current directory (or ``LAB_WIZARD_WORKSPACE``). ``params`` (a dict or a
    pydantic model) and ``metadata`` are recorded with the run and become
    filters on the Data page; ``units`` gives columns their units up front;
    ``plots`` are how the Data page draws the run, as a procedure's ``plots:``.
    ``resources``, if given, is snapshotted like a project's instruments.
    ``sinks`` are anything more the run should produce, such as a
    :class:`~lab_wizard.lib.savers.FileSaver`.

    The run ends ``success`` when the block finishes, ``aborted`` on Ctrl-C,
    and ``failed`` if it raises; the exception is not swallowed.
    """
    from lab_wizard.lib.data.read import lab_database
    from lab_wizard.lib.task_adapters.run import run_outputs

    outputs = run_outputs(database=database if database is not None else lab_database(), sinks=sinks or [])
    context = RunContext()
    outputs.attach(context.data_bus, context.status_bus)
    if hasattr(params, "model_dump"):
        params = params.model_dump(mode="json")
    context.data_bus.emit(
        RunStarted(
            procedure=procedure,
            device=device,
            operator=operator,
            notes=notes,
            metadata=dict(jsonable(metadata or {})),
            definition={"name": procedure, "plots": list(plots or [])},
            params=dict(jsonable(params or {})),
            instruments=baseline_snapshot(resources) if resources is not None else {},
            columns={name: {"unit": unit} for name, unit in (units or {}).items()},
        )
    )
    root = (procedure,)
    context.status_bus.emit(StepBegan(root, None, procedure, False, kind="script"))
    status = Status.FAILED
    error: str | None = None
    try:
        with context.executing(root):
            yield Recording(context)
        status = Status.SUCCESS
    except KeyboardInterrupt:
        status = Status.ABORTED
        raise
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            context.close_point()
            context.status_bus.emit(StepEnded(root, status.value, error=error))
            context.data_bus.emit(RunEnded(status.value))
        finally:
            outputs.close()
