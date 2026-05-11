"""Shared-flow game-master build contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from omegaconf import OmegaConf

from silisocs.environments.gm import shared_flow_game_master as shared_gm
from silisocs.environments.gm.shared_flow_game_master import MultiFlowSocialMediaGameMaster
from silisocs.runtime.config import ConfigStore


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


class _BuiltGM:
    def __init__(self, *, agent_name, act_component, context_components) -> None:
        self.agent_name = agent_name
        self.act_component = act_component
        self.context_components = context_components


def test_shared_flow_gm_uses_base_initialization_contracts(tmp_path, monkeypatch) -> None:
    """Shared-flow mode should keep backend init/logging behavior aligned with base GM."""
    cfg = OmegaConf.create(
        {
            "output_rootname": str(tmp_path),
            "sim": {
                "action_mode": "custom",
                "tool_calling": {"mode": "none"},
                "engine": {"action_loop": {"built_in": "open_ended"}},
            },
            "env": {
                "platform_type": "twitter_like",
                "use_server": False,
                "app": {
                    "class_path": "tests.fake.CustomApp",
                    "params": {"answer": 42},
                },
                "enabled_actions": ["POST"],
                "seed_posts": {"type": "none"},
                "social_network": {
                    "network_type": "barabasi_albert",
                    "barabasi_albert_m": 1,
                },
                "gm": {
                    "components": {
                        "next_acting": {"built_in": "all_entities"},
                        "observe": {"built_in": "timeline_every_turn"},
                        "resolve": {"built_in": "parsed_action"},
                        "recommend": {"built_in": "recommendation_component"},
                        "initializer": {"built_in": "backend_default"},
                    }
                },
            },
        }
    )
    ConfigStore.set_config(cfg)

    created: dict[str, Any] = {}
    app = _DummyApp()

    def fake_create_social_media_app(**kwargs: Any) -> _DummyApp:
        created.update(kwargs)
        return app

    monkeypatch.setattr(
        "silisocs.environments.backends.factory.create_social_media_app",
        fake_create_social_media_app,
    )
    monkeypatch.setattr(
        shared_gm.entity_agent_with_logging,
        "EntityAgentWithLogging",
        _BuiltGM,
    )
    monkeypatch.setattr(shared_gm, "build_next_acting_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_observe_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_resolve_component", lambda *a, **k: object())
    monkeypatch.setattr(shared_gm, "build_recommendation_component", lambda *a, **k: object())

    entities = [_DummyEntity("Alice Smith"), _DummyEntity("Bob Jones")]
    user_data = {
        "sim_role_parameters": {
            "activity_transition_rates": {
                "user": {"inactive_to_active": 0.7, "active_to_inactive": 0.2}
            }
        },
        "sim_roles": {"Alice Smith": "user", "Bob Jones": "user"},
        "entity_flow_tags": {"Alice Smith": "default", "Bob Jones": "default"},
        "gm_orchestration": {"gm_name": "gm_shared", "owned_flows": ["default"]},
    }
    gm = MultiFlowSocialMediaGameMaster(
        params={
            "name": "gm_shared",
            "calls_to_action": {"social_media_action": "Act"},
            "sm_user_data": user_data,
            "app_description": "desc",
        },
        entities=entities,
    )

    built = gm.build(model=object(), memory_bank=object())

    assert created["action_logger"] is not None
    assert created["db_path"] == str(tmp_path / "twitter_like.db")
    assert created["app_class_path"] == "tests.fake.CustomApp"
    assert created["app_params"] == {"answer": 42}
    assert app.enabled_actions == ["POST", "FINISHED"]
    assert app.initialized_with == {
        "agent_names": ["Alice Smith", "Bob Jones"],
        "sim_roles": {"Alice Smith": "user", "Bob Jones": "user"},
        "seed_posts": {"Alice Smith": "", "Bob Jones": ""},
        "social_network": {"network_type": "barabasi_albert", "barabasi_albert_m": 1},
    }
    assert built.act_component.activity_transition_rates == {
        "Alice Smith": {"inactive_to_active": 0.7, "active_to_inactive": 0.2},
        "Bob Jones": {"inactive_to_active": 0.7, "active_to_inactive": 0.2},
    }
    assert built.act_component.gm_orchestration == user_data["gm_orchestration"]
    assert built.act_component.shared_flow_mode is True
