from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from omegaconf import OmegaConf

from silisocs.environments.backends.base import BackendApp, app_action
from silisocs.environments.gm import base_game_master as base_gm
from silisocs.environments.gm.game_master import GameMaster
from silisocs.runtime.types import ActionOutput, OutputType


class _GenericApp(BackendApp):
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


def test_base_environment_gm_builds_without_social_config(tmp_path, monkeypatch) -> None:
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "generic",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": False},
                "engine": {"turn_policy": {"built_in": "single_action"}},
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
                        "update": {"built_in": "disabled"},
                    }
                },
            },
        }
    )
    app = _GenericApp()
    monkeypatch.setattr(base_gm, "create_environment_app", lambda **kwargs: app)

    entities = [_Entity("Alice"), _Entity("Bob")]
    built = GameMaster(
        model=object(),
        agents=entities,
        params={
            "name": "generic_gm",
            "calls_to_action": {"environment_action": "Act"},
            "environment_data": {
                "sim_role_parameters": {},
                "sim_roles": {"Alice": "worker", "Bob": "worker"},
                "agent_flow_tags": {"Alice": "default", "Bob": "default"},
                "gm_orchestration": {"gm_name": "generic_gm"},
            },
            "app_description": "Generic app",
            "runtime_config": OmegaConf.to_container(cfg, resolve=True),
            "initializer": {"built_in": "app_initialize", "class_path": None, "params": {}},
        },
    )

    assert built.name == "generic_gm"
    assert app.initialized_with is None
    assert hasattr(built, "acting_agents")
    assert hasattr(built, "action_prompt")
    assert hasattr(built, "make_observation")
    assert hasattr(built, "resolve_action")
    assert hasattr(built, "initialize")
    assert entities[0]._allowed_action_types is None
    assert entities[0].action_output_mode is None
    assert "Available actions:" in built.action_prompt_template
    assert set(built.components) >= {
        "next_acting",
        "observe",
        "resolve",
        "update",
    }

    turns = built.acting_agents(entities)
    assert turns == ["Alice"]
    action_spec = built.action_prompt("Alice")
    assert action_spec.output_type == OutputType.TEXT
    assert "Available actions:" in action_spec.prompt
    assert built.make_observation("Alice") == "Alice observes app state"
    assert built.resolve_action("Alice", ActionOutput.from_text("ACTION: ACT")) == "Alice acted"


def test_base_environment_gm_rejects_unknown_component_params(tmp_path, monkeypatch) -> None:
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "generic",
                "tool_calling": {"mode": "none"},
                "prompt_additions": {"action_count_guidance": False},
                "engine": {"turn_policy": {"built_in": "single_action"}},
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
                        "update": {"built_in": "disabled"},
                    }
                },
            },
        }
    )
    app = _GenericApp()
    monkeypatch.setattr(base_gm, "create_environment_app", lambda **kwargs: app)

    with pytest.raises(ValueError, match="Unsupported config param"):
        GameMaster(
            model=object(),
            agents=[_Entity("Alice")],
            params={
                "name": "generic_gm",
                "calls_to_action": {"environment_action": "Act"},
                "environment_data": {
                    "sim_role_parameters": {},
                    "sim_roles": {"Alice": "worker"},
                    "agent_flow_tags": {"Alice": "default"},
                    "gm_orchestration": {"gm_name": "generic_gm"},
                },
                "app_description": "Generic app",
                "runtime_config": OmegaConf.to_container(cfg, resolve=True),
                "initializer": {"built_in": "app_initialize", "class_path": None, "params": {}},
            },
        )
