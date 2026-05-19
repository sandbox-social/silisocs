"""Shared-flow game-master build contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omegaconf import OmegaConf

from silisocs.environments.gm import shared_flow_game_master as shared_gm
from silisocs.environments.gm.shared_flow_game_master import FlowRoutedGameMaster


@dataclass
class _DummyEntity:
    name: str
    _allowed_action_types: list[str] | None = None
    action_output_mode: str | None = None

    @property
    def _agent_name(self) -> str:
        return self.name

    def set_allowed_action_types(self, values: list[str]) -> None:
        self._allowed_action_types = values

    def set_action_output_mode(self, mode: str) -> None:
        self.action_output_mode = mode


class _DummyApp:
    def __init__(self) -> None:
        self.enabled_actions: list[str] | None = None
        self.initialized_with: dict[str, Any] | None = None

    def set_enabled_actions(self, actions: list[str]) -> None:
        self.enabled_actions = actions

    def action_catalog(self) -> list[dict[str, str]]:
        return [{"selectable_name": "POST"}, {"selectable_name": "LIKE"}]

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        self.initialized_with = {"agent_names": agent_names, **kwargs}


def test_shared_flow_gm_uses_base_initialization_contracts(tmp_path, monkeypatch) -> None:
    """Shared-flow mode should keep backend init/logging behavior aligned with base GM."""
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "none"},
                "engine": {"turn_policy": {"built_in": "open_ended"}},
            },
            "env": {
                "platform_type": "twitter_like",
                "use_server": False,
                "app": {
                    "class_path": "tests.fake.CustomApp",
                    "params": {"answer": 42},
                },
                "enabled_actions": ["POST"],
                "social_network": {
                    "network_type": "barabasi_albert",
                    "barabasi_albert_m": 1,
                },
                "gm": {
                    "components": {
                        "next_acting": {"built_in": "all_agents"},
                        "observe": {"built_in": "timeline_every_turn"},
                        "resolve": {"built_in": "parsed_action"},
                        "update": {"built_in": "social_recommendation"},
                    }
                },
            },
        }
    )
    created: dict[str, Any] = {}
    app = _DummyApp()

    def fake_create_social_media_app(**kwargs: Any) -> _DummyApp:
        created.update(kwargs)
        return app

    monkeypatch.setattr(
        "silisocs.environments.backends.factory.create_social_media_app",
        fake_create_social_media_app,
    )
    monkeypatch.setattr(shared_gm, "build_next_acting_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_observe_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_resolve_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_update_component", lambda *a, **k: object())

    entities = [_DummyEntity("Alice Smith"), _DummyEntity("Bob Jones")]
    user_data = {
        "sim_role_parameters": {
            "activity_transition_rates": {
                "user": {"inactive_to_active": 0.7, "active_to_inactive": 0.2}
            }
        },
        "sim_roles": {"Alice Smith": "user", "Bob Jones": "user"},
        "agent_flow_tags": {"Alice Smith": "default", "Bob Jones": "default"},
        "gm_orchestration": {"gm_name": "gm_shared", "owned_flows": ["default"]},
    }
    gm = FlowRoutedGameMaster(
        model=object(),
        agents=entities,
        params={
            "name": "gm_shared",
            "calls_to_action": {"social_media_action": "Act"},
            "sm_user_data": user_data,
            "app_description": "desc",
            "runtime_config": OmegaConf.to_container(cfg, resolve=True),
            "initializer": {"built_in": "social_media", "class_path": None, "params": {}},
        },
    )

    assert created["action_logger"] is not None
    assert created["db_path"] == str(tmp_path / "twitter_like.db")
    assert created["app_class_path"] == "tests.fake.CustomApp"
    assert created["app_params"] == {"answer": 42}
    assert app.initialized_with is None

    assert app.enabled_actions == ["POST", "FINISHED"]
    assert app.initialized_with is None
    assert gm.name == "gm_shared"
    assert gm.activity_transition_rates == {
        "Alice Smith": {"inactive_to_active": 0.7, "active_to_inactive": 0.2},
        "Bob Jones": {"inactive_to_active": 0.7, "active_to_inactive": 0.2},
    }
    assert gm.gm_orchestration == user_data["gm_orchestration"]
    assert gm.shared_flow_mode is True
    assert hasattr(gm, "acting_agents")
    assert hasattr(gm, "action_prompt")
    assert hasattr(gm, "make_observation")
    assert hasattr(gm, "resolve_action")
    assert hasattr(gm, "initialize")
    assert hasattr(gm, "update")
