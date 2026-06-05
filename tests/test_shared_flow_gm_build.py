"""Shared-flow game-master build contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from silisocs.environments.gm import game_master as gm_module
from silisocs.environments.gm.game_master import MultiFlowGameMaster


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
        self.excluded_actions: list[str] | None = None
        self.initialized_with: dict[str, Any] | None = None

    def set_enabled_actions(self, actions: list[str]) -> None:
        self.enabled_actions = actions

    def set_action_filters(
        self,
        *,
        enabled_actions: list[str] | None,
        excluded_actions: list[str] | None = None,
    ) -> None:
        self.enabled_actions = enabled_actions
        self.excluded_actions = excluded_actions

    def action_catalog(self) -> list[dict[str, str]]:
        return [{"selectable_name": "POST"}, {"selectable_name": "LIKE"}]

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        self.initialized_with = {"agent_names": agent_names, **kwargs}


def test_shared_flow_gm_uses_base_initialization_contracts(tmp_path, monkeypatch) -> None:
    """Shared-flow mode should keep backend init/logging behavior aligned with base GM."""
    created: dict[str, Any] = {}
    app = _DummyApp()

    def fake_create_backend_app(**kwargs: Any) -> _DummyApp:
        created.update(kwargs)
        return app

    monkeypatch.setattr(gm_module, "create_backend_app", fake_create_backend_app)

    agents = [_DummyEntity("Alice Smith"), _DummyEntity("Bob Jones")]
    gm = MultiFlowGameMaster(
        model=object(),
        agents=agents,
        name="gm_shared",
        sim_roles={"Alice Smith": "user", "Bob Jones": "user"},
        agent_flow_tags={"Alice Smith": "default", "Bob Jones": "default"},
        owned_flows=["default"],
        backend_config={
            "backend_type": "twitter_like",
            "output_rootname": str(tmp_path),
            "enabled_actions": ["POST"],
            "turn_policy_built_in": "open_ended",
            "app_description": "desc",
            "class_path": "tests.fake.CustomApp",
            "params": {"answer": 42},
        },
        components={
            "initialize": {"built_in": "social_media", "class_path": None, "params": {}},
            "next_acting": {
                "built_in": "all_agents",
                "params": {},
            },
            "observe": {"built_in": "timeline_every_turn"},
            "resolve": {"built_in": "parsed_action"},
            "update": {"built_in": "social_recommendation"},
        },
        action_prompt_template="Act",
        action_mode="custom",
        tool_calling_mode="none",
    )

    assert created["action_logger"] is not None
    assert created["db_path"] == str(tmp_path / "twitter_like.db")
    assert created["class_path"] == "tests.fake.CustomApp"
    assert created["params"] == {"answer": 42}
    assert app.initialized_with is None

    assert app.enabled_actions == ["POST", "FINISHED"]
    assert app.initialized_with is None
    assert gm.name == "gm_shared"
    assert gm.owned_flows == ("default",)
    assert hasattr(gm, "acting_agents")
    assert hasattr(gm, "action_prompt")
    assert hasattr(gm, "make_observation")
    assert hasattr(gm, "resolve_action")
    assert hasattr(gm, "initialize")
    assert hasattr(gm, "update")
