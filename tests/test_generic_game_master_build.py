from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from silisocs.environments.backends.base import BackendApp, app_action
from silisocs.environments.gm import game_master as gm_module
from silisocs.environments.gm.game_master import ComponentGameMaster
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
    app = _GenericApp()
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: app)

    agents = [_Entity("Alice"), _Entity("Bob")]
    built = ComponentGameMaster(
        model=object(),
        agents=agents,
        name="generic_gm",
        sim_roles={"Alice": "worker", "Bob": "worker"},
        agent_flow_tags={"Alice": "default", "Bob": "default"},
        backend_config={
            "backend_type": "resource_market",
            "output_rootname": str(tmp_path),
            "enabled_actions": ["ACT"],
            "turn_policy_built_in": "single_action",
            "app_description": "Generic app",
        },
        components={
            "initialize": {"built_in": "app_initialize", "class_path": None, "params": {}},
            "next_acting": {"built_in": "fixed_order"},
            "observe": {"built_in": "app_observation", "params": {"limit": 3}},
            "resolve": {"built_in": "generic_action"},
            "update": {"built_in": "disabled"},
        },
        action_mode="generic",
        tool_calling_mode="none",
        prompt_config={"add_action_count_guidance": False},
    )

    assert built.name == "generic_gm"
    assert app.initialized_with is None
    assert hasattr(built, "acting_agents")
    assert hasattr(built, "action_prompt")
    assert hasattr(built, "make_observation")
    assert hasattr(built, "resolve_action")
    assert hasattr(built, "initialize")
    assert agents[0]._allowed_action_types is None
    assert agents[0].action_output_mode is None
    assert "Available actions:" in built.action_prompt_template
    assert set(built.components) >= {
        "next_acting",
        "observe",
        "resolve",
        "update",
    }

    turns = built.acting_agents(agents)
    assert turns == ["Alice"]
    action_spec = built.action_prompt("Alice")
    assert action_spec.output_type == OutputType.TEXT
    assert "Available actions:" in action_spec.prompt
    assert built.make_observation("Alice") == "Alice observes app state"
    assert built.resolve_action("Alice", ActionOutput.from_text("ACTION: ACT")) == "Alice acted"


def test_base_environment_gm_rejects_unknown_component_params(tmp_path, monkeypatch) -> None:
    app = _GenericApp()
    monkeypatch.setattr(gm_module, "create_backend_app", lambda **kwargs: app)

    with pytest.raises(ValueError, match="Unsupported config param"):
        ComponentGameMaster(
            model=object(),
            agents=[_Entity("Alice")],
            name="generic_gm",
            sim_roles={"Alice": "worker"},
            agent_flow_tags={"Alice": "default"},
            backend_config={
                "backend_type": "resource_market",
                "output_rootname": str(tmp_path),
                "enabled_actions": ["ACT"],
                "turn_policy_built_in": "single_action",
                "app_description": "Generic app",
            },
            components={
                "initialize": {"built_in": "app_initialize", "class_path": None, "params": {}},
                "next_acting": {"built_in": "fixed_order", "params": {"typo": True}},
                "observe": {"built_in": "app_observation"},
                "resolve": {"built_in": "generic_action"},
                "update": {"built_in": "disabled"},
            },
            action_mode="generic",
            tool_calling_mode="none",
        )
