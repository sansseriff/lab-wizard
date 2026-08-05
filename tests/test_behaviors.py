"""Behavior interfaces: registration, ordering, and end-to-end coverage.

The failure this guards against is silent by nature. An unregistered behavior
reports ``behavior_abc: None``, which reads as "this instrument does nothing in
particular" — so the instrument still appears in every listing, and simply
cannot be matched to any measurement requirement. Nothing errors. The test that
catches it has to be the one that walks the whole tree and asserts coverage.
"""

import importlib
from pathlib import Path
import pkgutil

import pytest

import lab_wizard.lib.instruments as instruments_pkg
from lab_wizard.lib.client.proxies.registry import (
    PROXY_BY_BEHAVIOR,
    PROXY_BY_BEHAVIOR_ABC,
    PROXY_EXEMPT,
    proxy_class_for,
)
from lab_wizard.lib.client.proxies.base import RemoteOpaque, RemoteProxy
from lab_wizard.lib.instruments.general.behavior import (
    CONTAINER,
    TERMINAL,
    InstrumentBehavior,
    behavior_name_for,
    behaviors,
    register_behavior,
)
from lab_wizard.lib.instruments.general.counter import Counter
from lab_wizard.lib.instruments.general.parent_child import ChannelProvider
from lab_wizard.lib.instruments.general.vsense import VSense
from lab_wizard.lib.instruments.general.vsource import VSource


# Modules that do not import, and are known dead rather than newly broken.
# thorLabsPM100D subclasses ``GenericSense``, a name that predates the rename to
# ``VSense`` and has never existed in this tree; the module defines no Params
# class, so discovery has never registered it and nothing imports it. Listed
# rather than tolerated silently, so a *new* unimportable module still fails —
# an instrument that cannot be imported cannot register its behaviors, which is
# precisely how a missing proxy would slip past this file.
KNOWN_UNIMPORTABLE = {"lab_wizard.lib.instruments.thorLabsPM100D"}


def _import_every_instrument_module() -> list[str]:
    """Import the whole instrument package so every behavior has registered.

    Production never needs this — a class cannot exist without its bases having
    been imported — but a *coverage check* has to see behaviors nobody happens
    to have loaded yet, which is exactly the one that would be missing a proxy.
    """
    failed: list[str] = []
    for mod in pkgutil.walk_packages(
        instruments_pkg.__path__, prefix=f"{instruments_pkg.__name__}."
    ):
        if mod.name in KNOWN_UNIMPORTABLE:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            failed.append(f"{mod.name}: {exc}")
    return failed


# --------------------------- registration ---------------------------


def test_the_four_shipped_behaviors_are_registered():
    registered = dict(behaviors())
    for abc in (VSource, VSense, Counter, ChannelProvider):
        assert abc.__name__ in registered
        assert registered[abc.__name__] is abc


def test_terminal_behaviors_are_checked_before_containers():
    """A channel that both sources voltage and holds sub-channels must report
    the capability a measurement can bind to, not the container."""
    names = [name for name, _ in behaviors()]
    assert names.index("VSource") < names.index("ChannelProvider")
    assert names.index("VSense") < names.index("ChannelProvider")
    assert names.index("Counter") < names.index("ChannelProvider")


def test_ordering_is_stable_across_calls():
    """The server reports a name and the client resolves it; if the order could
    shift between processes the two would disagree about one instrument."""
    assert behaviors() == behaviors()


def test_concrete_instruments_do_not_become_behaviors():
    """Only classes passing ``specificity`` register, so a driver inheriting
    VSource does not turn into a behavior of its own."""

    class MyDriver(VSource):
        def set_voltage(self, voltage: float) -> bool: ...
        def turn_on(self) -> bool: ...
        def turn_off(self) -> bool: ...

    assert MyDriver.__name__ not in dict(behaviors())


def test_re_registering_with_a_different_rank_is_refused():
    class Wobbly(InstrumentBehavior, specificity=TERMINAL):
        pass

    try:
        with pytest.raises(ValueError, match="already registered"):
            register_behavior(Wobbly, CONTAINER)
    finally:
        from lab_wizard.lib.instruments.general.behavior import _REGISTERED

        _REGISTERED.pop(Wobbly, None)


# --------------------------- classification ---------------------------


def test_a_class_is_classified_without_being_instantiated():
    """Registry indexing is lazy — classification must not open hardware."""
    assert behavior_name_for(Counter, is_class=True) == "Counter"


def test_an_unrelated_class_has_no_behavior():
    assert behavior_name_for(object(), is_class=False) is None


# --------------------------- proxy coverage ---------------------------


def test_every_registered_behavior_has_a_proxy_or_a_stated_exemption():
    """The check that makes a forgotten proxy loud.

    Without it, adding a behavior and not writing its proxy silently degrades
    every remote instance of it to RemoteOpaque: reflective calls still work, so
    nothing raises, but `isinstance` fails and the editor knows nothing about it.
    """
    import_failures = _import_every_instrument_module()
    assert not import_failures, (
        "instrument modules failed to import, so behavior coverage could not be "
        f"checked: {import_failures}"
    )

    uncovered = [
        name
        for name, _ in behaviors()
        if name not in PROXY_BY_BEHAVIOR_ABC and name not in PROXY_EXEMPT
    ]
    assert not uncovered, (
        f"behaviors with no client proxy: {uncovered}. Add a one-line proxy "
        "under lib/client/proxies/ and register it in PROXY_BY_BEHAVIOR, or "
        "record why it needs none in PROXY_EXEMPT."
    )


def test_the_name_keyed_view_is_derived_not_typed():
    """Keying on the ABC class is what stops the two ends drifting apart."""
    assert PROXY_BY_BEHAVIOR_ABC == {
        abc.__name__: proxy for abc, proxy in PROXY_BY_BEHAVIOR.items()
    }


def test_a_counter_resolves_to_a_typed_proxy():
    """The concrete gap this closes: pcr_curve needs a Counter, and before this
    a remote one reported no behavior and could not be selected at all."""
    proxy_cls = proxy_class_for("Counter")
    assert proxy_cls is not RemoteOpaque
    assert issubclass(proxy_cls, Counter)
    assert issubclass(proxy_cls, RemoteProxy)
    # Forwarders were injected for every abstract method, so it is instantiable.
    assert proxy_cls.__abstractmethods__ == frozenset()


def test_an_exempt_behavior_still_falls_back_rather_than_failing():
    assert proxy_class_for("ChannelProvider") is RemoteOpaque
    assert proxy_class_for(None) is RemoteOpaque


# --------------------------- measurement params ---------------------------
#
# Same class of bug as the behavior lists: measurements are discovered from the
# directory, so any table keyed by measurement name is a second list that goes
# quietly out of date.


def test_shipped_measurements_still_get_their_defaults():
    from lab_wizard.wizard.backend.project_generation import _measurement_param_defaults

    assert set(_measurement_param_defaults("iv_curve")) == {"bias", "readout", "safety"}
    assert set(_measurement_param_defaults("pcr_curve")) == {"bias", "readout"}


def test_a_new_measurement_gets_its_params_without_touching_the_generator(tmp_path):
    """The regression that matters: adding a measurement must not require an
    edit in project_generation for its params block to be populated."""
    from lab_wizard.wizard.backend.get_measurements import params_model_for_measurement
    from lab_wizard.wizard.backend.models import MeasurementInfo

    pkg = tmp_path / "lib" / "measurements" / "widget_sweep"
    pkg.mkdir(parents=True)
    (tmp_path / "lib" / "__init__.py").write_text("")
    (tmp_path / "lib" / "measurements" / "__init__.py").write_text("")
    (pkg / "__init__.py").write_text("")
    (pkg / "widget_sweep_setup_template.py").write_text(
        "from dataclasses import dataclass, field\n"
        "from pydantic import BaseModel\n"
        "\n"
        "class WidgetSweepParams(BaseModel):\n"
        "    spin: float = 2.5\n"
        "    label: str = 'default'\n"
        "\n"
        "@dataclass\n"
        "class WidgetSweepResources:\n"
        "    params: WidgetSweepParams = field(default_factory=WidgetSweepParams)\n",
        encoding="utf-8",
    )

    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        # The template resolves as lab_wizard.lib.<...>, so mirror that layout.
        import lab_wizard.lib.measurements as real_measurements

        target = Path(real_measurements.__path__[0]) / "widget_sweep"
        target.mkdir(exist_ok=True)
        (target / "__init__.py").write_text("")
        (target / "widget_sweep_setup_template.py").write_text(
            (pkg / "widget_sweep_setup_template.py").read_text(), encoding="utf-8"
        )
        try:
            model = params_model_for_measurement(
                MeasurementInfo(
                    name="widget_sweep",
                    description="",
                    measurement_dir=target,
                    measurement_file=target / "widget_sweep.py",
                )
            )
            assert model is not None, "params model was not found on the template"
            assert model().model_dump() == {"spin": 2.5, "label": "default"}
        finally:
            import shutil

            shutil.rmtree(target, ignore_errors=True)
    finally:
        sys.path.remove(str(tmp_path))
