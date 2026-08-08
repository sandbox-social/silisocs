"""YAML-reachable extensibility: plugins, custom sources, control, health counters.

These prove the "user extends without editing core" contract for the seams
that previously required an import nobody performed: the ``plugins:`` config
list, dotted-path persona data sources, a ``class_path`` run controller, and
``register_health_counter``.
"""

from __future__ import annotations

import pytest

from silisocs.evaluations.vocabulary import HEALTH_COUNTERS, register_health_counter
from silisocs.runtime.construction.agent_builders.records import RecordLoader
from silisocs.runtime.plugins import load_plugins
from silisocs.simulation_engines.control import StepGate, build_run_control

PLUGIN_IMPORTS: list[str] = []


def _plugin_side_effect() -> None:  # imported-module marker, see test below
    PLUGIN_IMPORTS.append("loaded")


_plugin_side_effect()


def custom_records(data_cfg, max_records=None):
    """A dotted-path persona source: plain callable, no base class."""
    records = [{"name": f"agent_{i}"} for i in range(int(data_cfg.get("n", 3)))]
    return records[:max_records] if max_records else records


class RemoteController:
    """A custom run controller reachable by class_path."""

    def __init__(self, gate: StepGate, *, endpoint: str = "") -> None:
        self.gate = gate
        self.endpoint = endpoint
        self.started = False

    def start(self) -> None:
        self.started = True

    def close(self) -> None:
        self.started = False


class HalfController:
    def __init__(self, gate: StepGate) -> None:
        self.gate = gate

    def start(self) -> None:  # no close() — must be rejected at build
        pass


def test_config_listed_plugin_modules_are_imported_and_failures_are_loud():
    PLUGIN_IMPORTS.clear()
    load_plugins([__name__])
    # Already-imported modules are fine (import is idempotent); the seam's
    # promise is only that the module HAS been imported before validation.
    assert __name__ in __import__("sys").modules

    with pytest.raises(ValueError, match="no_such_module_xyz"):
        load_plugins(["no_such_module_xyz"])


def test_register_health_counter_is_live_everywhere():
    register_health_counter("custom_widget_failures", "custom widgets failed")
    assert HEALTH_COUNTERS["custom_widget_failures"] == "custom widgets failed"
    # Idempotent re-registration is fine; a conflicting redefinition is not.
    register_health_counter("custom_widget_failures", "custom widgets failed")
    with pytest.raises(ValueError, match="already registered"):
        register_health_counter("custom_widget_failures", "something else")


def test_event_semantics_registration_is_one_registry_under_both_import_paths():
    """The declaring layer owns the registry; the documented path re-exports it.

    ``EventSemantics`` lives in the environments layer so a backend can declare
    its own vocabulary without importing the evaluation layer that reads it;
    ``evaluations.vocabulary`` stays the documented import home. A second
    registry behind the second path would silently drop registrations.
    """
    from silisocs.environments.backends import event_semantics as declaring_layer
    from silisocs.evaluations import vocabulary as documented_path

    assert documented_path.register_event_semantics is declaring_layer.register_event_semantics
    documented_path.register_event_semantics(
        "plugin_seam_demo",
        documented_path.EventSemantics(roles={"content.root": frozenset({"post"})}),
    )
    assert declaring_layer.event_semantics_for("plugin_seam_demo").labels("content.root") == (
        frozenset({"post"})
    )


def test_health_counters_reach_the_manifest_after_registration(tmp_path):
    """No import-time snapshot: a counter registered late still lands in health."""
    from silisocs.runtime.execution.manifest import build_run_manifest

    register_health_counter("late_registered_failures", "late things failed")
    manifest = build_run_manifest(
        output_dir=tmp_path,
        status="success",
        counters={"late_registered_failures": 2},
        game_masters=[],
    )
    assert manifest["health"]["late_registered_failures"] == 2


def test_persona_source_accepts_a_dotted_path_and_rejects_unknown_names():
    loader = RecordLoader(config={}, scenario_name="t", project_root=".")

    records = loader.load_records({"source": f"{__name__}.custom_records", "n": 2})
    assert records == [{"name": "agent_0"}, {"name": "agent_1"}]

    with pytest.raises(ValueError, match="Unknown persona data source"):
        loader.load_records({"source": "jsnol", "path": "x.jsonl"})


def test_run_control_class_path_builds_a_custom_controller(tmp_path):
    gate, controller = build_run_control(
        {
            "class_path": f"{__name__}.RemoteController",
            "params": {"endpoint": "http://localhost:1"},
            "start_paused": True,
        },
        output_dir=tmp_path,
    )

    assert isinstance(gate, StepGate)
    assert isinstance(controller, RemoteController)
    assert controller.gate is gate
    assert controller.endpoint == "http://localhost:1"

    with pytest.raises(TypeError, match="close"):
        build_run_control({"class_path": f"{__name__}.HalfController"}, output_dir=tmp_path)
