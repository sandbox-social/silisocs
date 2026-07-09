from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any

import pytest

from silisocs.environments.backends.base import BackendApp, app_action
from silisocs.environments.gm import game_master as gm_module
from silisocs.environments.gm.components.base import (
    BaseComponent,
    NextActingComponent,
    ObservationComponent,
)
from silisocs.environments.gm.game_master import ComponentGameMaster, MultiFlowGameMaster


def test_base_component_checkpoint_hooks_are_noop() -> None:
    component = BaseComponent()

    assert component.get_state() == {}
    component.set_state({"anything": "ignored"})
    assert component.get_state() == {}


def test_removed_native_component_lifecycle_symbols_are_absent() -> None:
    base = importlib.import_module("silisocs.environments.gm.components.base")

    for name in ("LoggingComponent", "RuntimeComponent", "FlowComponent"):
        assert not hasattr(base, name)
    for name in ("pre_act", "post_act", "set_entity", "get_entity"):
        assert not hasattr(BaseComponent, name)


# ------------------------------------------------------- component hot-swap (rebuild_component)


class _App(BackendApp):
    def name(self) -> str:
        return "generic"

    def description(self) -> str:
        return "Generic app"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del agent_names, kwargs

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        del kwargs
        return f"{actor_name} obs"

    @app_action(selectable_name="ACT", description="Act")
    def act(self, agent_name: str) -> str:
        return f"{agent_name} acted"


@dataclass
class _Ent:
    name: str

    def set_allowed_action_types(self, values: list[str]) -> None:
        del values

    def set_action_output_mode(self, mode: str) -> None:
        del mode


def _make_gm(tmp_path: Any, monkeypatch: Any, **component_overrides: Any) -> ComponentGameMaster:
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: _App())
    components: dict[str, Any] = {
        "initialize": {"built_in": "app_initialize"},
        "next_acting": {"built_in": "all_agents"},
        "observe": {"built_in": "app_observation", "params": {"limit": 3}},
        "resolve": {"built_in": "generic_action"},
        "update": {"built_in": "disabled"},
    }
    components.update(component_overrides)
    return ComponentGameMaster(
        model=object(),
        agents=[_Ent("Alice"), _Ent("Bob")],
        name="gm",
        sim_roles={"Alice": "worker", "Bob": "worker"},
        agent_flow_tags={"Alice": "default", "Bob": "default"},
        backend_config={
            "backend_type": "resource_market",
            "output_rootname": str(tmp_path),
            "enabled_actions": ["ACT"],
            "turn_policy_built_in": "single_action",
        },
        components=components,
        action_mode="generic",
        tool_calling_mode="none",
        prompt_config={"add_action_count_guidance": False},
    )


class _StatefulObserve(ObservationComponent):
    def make_observation(self, agent_name: str) -> str:
        del agent_name
        return ""

    def get_state(self) -> dict[str, Any]:
        return {"seen": 1}


def test_rebuild_component_swaps_registry_and_typed_attr(tmp_path, monkeypatch) -> None:
    # Both observe built-ins are stateless, so the swap passes the stateless guard.
    gm = _make_gm(tmp_path, monkeypatch)
    assert type(gm.observe_component).__name__ == "AppObservationComponent"
    gm.rebuild_component("observe", {"built_in": "episode_only"})
    # BOTH the typed slot attribute and the registry entry (the runtime routing
    # surface) point at the rebuilt component.
    assert type(gm.observe_component).__name__ == "EpisodeObservation"
    assert type(gm.components["observe"]).__name__ == "EpisodeObservation"
    assert gm.get_component("observe") is gm.observe_component


def test_rebuild_component_rejects_non_whitelisted_role(tmp_path, monkeypatch) -> None:
    gm = _make_gm(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="rebuild_component supports"):
        gm.rebuild_component("resolve", {"built_in": "generic_action"})


def test_rebuild_component_refuses_stateful_outgoing(tmp_path, monkeypatch) -> None:
    gm = _make_gm(tmp_path, monkeypatch)

    class _StatefulNextActing(NextActingComponent):
        def acting_agent_names(self) -> list[str]:
            return []

        def get_state(self) -> dict[str, Any]:
            return {"cursor": 3}

    gm._component_registry["next_acting"] = _StatefulNextActing()
    with pytest.raises(ValueError, match="Cannot hot-swap stateful"):
        gm.rebuild_component("next_acting", {"built_in": "all_agents"})


def test_rebuild_component_refuses_stateful_incoming(tmp_path, monkeypatch) -> None:
    gm = _make_gm(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="stateful 'observe' component"):
        gm.rebuild_component("observe", {"class_path": f"{__name__}._StatefulObserve"})


def test_checkpoint_skips_component_state_on_class_mismatch(tmp_path, monkeypatch, caplog) -> None:
    gm = _make_gm(tmp_path, monkeypatch)

    class _StatefulA(NextActingComponent):
        def acting_agent_names(self) -> list[str]:
            return []

        def get_state(self) -> dict[str, Any]:
            return {"cursor": 5}

        def set_state(self, state: Any) -> None:
            self.applied = dict(state)

    gm._component_registry["next_acting"] = _StatefulA()
    saved = gm.get_state()
    # The stateful component's class identity is recorded alongside its state.
    assert saved["component_classes"]["next_acting"].endswith("_StatefulA")

    class _OtherNextActing(NextActingComponent):
        def acting_agent_names(self) -> list[str]:
            return []

        def set_state(self, state: Any) -> None:
            self.applied = dict(state)

    other = _OtherNextActing()  # simulate a mid-run swap having changed the class
    gm._component_registry["next_acting"] = other
    with caplog.at_level("WARNING"):
        gm.set_state(saved)
    assert not hasattr(other, "applied"), "foreign checkpoint state must be skipped"
    assert "swap_component changed it" in caplog.text


def test_checkpoint_stateless_gm_has_no_component_classes_key(tmp_path, monkeypatch) -> None:
    # A run with no stateful components keeps the exact legacy payload (additive key).
    gm = _make_gm(tmp_path, monkeypatch)
    assert "component_classes" not in gm.get_state()


def test_rebuild_component_targets_default_flow_key_on_multiflow(tmp_path, monkeypatch) -> None:
    # A MultiFlowGameMaster whose default flow is remapped to a flow-specialized
    # observe instance: the swap must re-point the key the default flow ACTUALLY
    # routes through, not the bare 'observe' key (which no agent reads).
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: _App())
    gm = MultiFlowGameMaster(
        model=object(),
        agents=[_Ent("Alice"), _Ent("Bob")],
        name="gm",
        sim_roles={"Alice": "worker", "Bob": "worker"},
        agent_flow_tags={"Alice": "default", "Bob": "default"},
        backend_config={
            "backend_type": "resource_market",
            "output_rootname": str(tmp_path),
            "enabled_actions": ["ACT"],
            "turn_policy_built_in": "single_action",
        },
        components={
            "initialize": {"built_in": "app_initialize"},
            "next_acting": {"built_in": "all_agents"},
            "observe": {
                "built_in": "app_observation",
                "params": {"limit": 3},
                "instances": {"special": {"built_in": "episode_only"}},
                "flow_map": {"default": "special"},
            },
            "resolve": {"built_in": "generic_action"},
            "update": {"built_in": "disabled"},
        },
        action_mode="generic",
        tool_calling_mode="none",
        prompt_config={"add_action_count_guidance": False},
    )
    routed_key = gm.flow_to_component_map["default"]["observe"]
    assert routed_key == "observe__special"
    assert type(gm.observe_component).__name__ == "EpisodeObservation"

    gm.rebuild_component("observe", {"built_in": "timeline_every_turn"})
    # The swap re-points the flow-routed key AND the typed slot; a bare 'observe'
    # key (unread by the default flow) would have been a silent no-op.
    assert type(gm.components[routed_key]).__name__ == "TimelineMakeObservation"
    assert type(gm.observe_component).__name__ == "TimelineMakeObservation"
