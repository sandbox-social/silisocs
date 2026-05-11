from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from omegaconf import OmegaConf

from silisocs.environments.backends.base import EnvironmentApp, app_action
from silisocs.environments.gm import base_game_master as base_gm
from silisocs.environments.gm.base_game_master import BaseEnvironmentGameMaster
from silisocs.runtime.config import ConfigStore


class _GenericApp(EnvironmentApp):
    def __init__(self) -> None:
        super().__init__()
        self.initialized_with: dict[str, Any] | None = None

    def name(self) -> str:
        return "generic"

    def description(self) -> str:
        return "Generic app"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        self.initialized_with = {"agent_names": agent_names, **kwargs}

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        del kwargs
        return f"{actor_name} observes app state"

    @app_action(selectable_name="ACT", description="Act")
    def act(self, current_user: str) -> str:
        return f"{current_user} acted"


@dataclass
class _Entity:
    name: str
    _allowed_action_types: list[str] | None = None
    action_output_mode: str | None = None

    def set_allowed_action_types(self, values: list[str]) -> None:
        self._allowed_action_types = values

    def set_action_output_mode(self, mode: str) -> None:
        self.action_output_mode = mode


class _BuiltGM:
    def __init__(self, *, agent_name, act_component, context_components) -> None:
        self.agent_name = agent_name
        self.act_component = act_component
        self.context_components = context_components


def test_base_environment_gm_builds_without_social_config(tmp_path, monkeypatch) -> None:
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "generic",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": False},
                "engine": {"action_loop": {"built_in": "single_action"}},
            },
            "env": {
                "platform_type": "resource_market",
                "use_server": False,
                "enabled_actions": ["ACT"],
                "app": {"class_path": None, "params": {}},
                "gm": {
                    "components": {
                        "next_acting": {"built_in": "fixed_order"},
                        "observe": {"built_in": "app_observation", "params": {"limit": 3}},
                        "resolve": {"built_in": "generic_action"},
                        "recommend": {"built_in": "disabled"},
                        "initializer": {"built_in": "backend_default"},
                    }
                },
            },
        }
    )
    ConfigStore.set_config(cfg)

    app = _GenericApp()
    monkeypatch.setattr(base_gm, "create_environment_app", lambda **kwargs: app)
    monkeypatch.setattr(base_gm.entity_agent_with_logging, "EntityAgentWithLogging", _BuiltGM)

    entities = [_Entity("Alice"), _Entity("Bob")]
    gm = BaseEnvironmentGameMaster(
        params={
            "name": "generic_gm",
            "calls_to_action": {"environment_action": "Act"},
            "environment_data": {
                "sim_role_parameters": {},
                "sim_roles": {"Alice": "worker", "Bob": "worker"},
                "entity_flow_tags": {"Alice": "default", "Bob": "default"},
                "gm_orchestration": {"gm_name": "generic_gm"},
            },
            "app_description": "Generic app",
        },
        entities=entities,
    )

    built = gm.build(model=object(), memory_bank=object())

    assert built.agent_name == "generic_gm"
    assert app.initialized_with == {
        "agent_names": ["Alice", "Bob"],
        "sim_roles": {"Alice": "worker", "Bob": "worker"},
        "seed_posts": {"Alice": "", "Bob": ""},
        "social_network": {},
    }
    assert entities[0]._allowed_action_types == ["ACT"]
    assert entities[0].action_output_mode == "generic_action"
    assert "Available actions:" in built.act_component.call_to_action_str


def test_base_environment_gm_rejects_unknown_component_params(tmp_path, monkeypatch) -> None:
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "generic",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": False},
                "engine": {"action_loop": {"built_in": "single_action"}},
            },
            "env": {
                "platform_type": "resource_market",
                "use_server": False,
                "enabled_actions": ["ACT"],
                "app": {"class_path": None, "params": {}},
                "gm": {
                    "components": {
                        "next_acting": {"built_in": "fixed_order", "params": {"typo": True}},
                        "observe": {"built_in": "app_observation"},
                        "resolve": {"built_in": "generic_action"},
                        "recommend": {"built_in": "disabled"},
                        "initializer": {"built_in": "backend_default"},
                    }
                },
            },
        }
    )
    ConfigStore.set_config(cfg)

    app = _GenericApp()
    monkeypatch.setattr(base_gm, "create_environment_app", lambda **kwargs: app)
    monkeypatch.setattr(base_gm.entity_agent_with_logging, "EntityAgentWithLogging", _BuiltGM)

    gm = BaseEnvironmentGameMaster(
        params={
            "name": "generic_gm",
            "calls_to_action": {"environment_action": "Act"},
            "environment_data": {
                "sim_role_parameters": {},
                "sim_roles": {"Alice": "worker"},
                "entity_flow_tags": {"Alice": "default"},
                "gm_orchestration": {"gm_name": "generic_gm"},
            },
            "app_description": "Generic app",
        },
        entities=[_Entity("Alice")],
    )

    with pytest.raises(ValueError, match="Unsupported config param"):
        gm.build(model=object(), memory_bank=object())
