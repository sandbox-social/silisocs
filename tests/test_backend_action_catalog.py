from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from silisocs.environments.backends.base import SocialBackendApp, app_action


@dataclass
class _FakeApp(SocialBackendApp):
    def __post_init__(self) -> None:
        super().__init__()

    def name(self) -> str:
        return "FakeApp"

    def description(self) -> str:
        return "Fake app for tests"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del agent_names, kwargs

    @app_action(selectable_name="post_message", description="Post a message")
    def create_tweet(self, agent_name: str, status: str) -> str:
        return f"{agent_name}:{status}"

    @app_action
    def like_tweet(self, agent_name: str, post_id: int) -> str:
        return f"{agent_name}:{post_id}"


def test_action_catalog_exposes_selectable_names() -> None:
    app = _FakeApp()
    catalog = app.action_catalog()

    names = {item["name"] for item in catalog}
    selectable = {item["selectable_name"] for item in catalog}

    assert "create_tweet" in names
    assert "post_message" in selectable
    post = next(item for item in catalog if item["selectable_name"] == "post_message")
    assert [param["name"] for param in post["parameters"]] == ["status"]


def test_enabled_action_filtering_accepts_aliases() -> None:
    app = _FakeApp()
    app.set_enabled_actions(["post_message"])

    actions = app.actions()
    assert len(actions) == 1
    assert actions[0].name == "create_tweet"


def test_excluded_action_filtering_accepts_aliases() -> None:
    app = _FakeApp()
    app.set_excluded_actions(["post_message"])

    selectable = {action.selectable_name for action in app.actions()}
    assert "post_message" not in selectable
    assert "like_tweet" in selectable
    assert "FINISHED" in selectable


def test_action_filters_reject_unknown_names() -> None:
    app = _FakeApp()

    with pytest.raises(ValueError, match="Unknown excluded action"):
        app.set_excluded_actions(["not_an_action"])


def test_action_filters_reject_allow_deny_conflicts() -> None:
    app = _FakeApp()

    with pytest.raises(ValueError, match="both enabled and excluded"):
        app.set_action_filters(
            enabled_actions=["create_tweet"],
            excluded_actions=["post_message"],
        )


def test_invoke_action_with_kwargs_supports_selectable_name() -> None:
    app = _FakeApp()
    output = app.invoke_action_with_kwargs(
        "post_message",
        {"agent_name": "Alice Smith", "status": "Hello"},
    )

    assert output == "Alice Smith:Hello"


def test_agent_facing_prompts_and_tools_omit_runtime_actor_arg() -> None:
    app = _FakeApp()

    prompt = app.generate_generic_action_prompt()
    tools = app.generate_tool_schemas()

    assert "agent_name" not in prompt
    post_schema = next(tool for tool in tools if tool["function"]["name"] == "post_message")
    params = post_schema["function"]["parameters"]
    assert list(params["properties"]) == ["status"]
    assert params["required"] == ["status"]


def test_finished_action_is_available_and_invokable() -> None:
    app = _FakeApp()
    selectable = {item["selectable_name"] for item in app.action_catalog()}

    assert "FINISHED" in selectable
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"
