from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mastodon_sim.environments.backends.base import SocialMediaApp, app_action


@dataclass
class _FakeApp(SocialMediaApp):
    def name(self) -> str:
        return "FakeApp"

    def description(self) -> str:
        return "Fake app for tests"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        del agent_names, kwargs

    @app_action(selectable_name="post_message", description="Post a message")
    def create_tweet(self, current_user: str, status: str) -> str:
        return f"{current_user}:{status}"

    @app_action
    def like_tweet(self, current_user: str, post_id: int) -> str:
        return f"{current_user}:{post_id}"


def test_action_catalog_exposes_selectable_names() -> None:
    app = _FakeApp()
    catalog = app.action_catalog()

    names = {item["name"] for item in catalog}
    selectable = {item["selectable_name"] for item in catalog}

    assert "create_tweet" in names
    assert "post_message" in selectable


def test_enabled_action_filtering_accepts_aliases() -> None:
    app = _FakeApp()
    app.set_enabled_actions(["post_message"])

    actions = app.actions()
    assert len(actions) == 1
    assert actions[0].name == "create_tweet"


def test_invoke_action_with_kwargs_supports_selectable_name() -> None:
    app = _FakeApp()
    output = app.invoke_action_with_kwargs(
        "post_message",
        {"current_user": "Alice Smith", "status": "Hello"},
    )

    assert output == "Alice Smith:Hello"


def test_finished_action_is_available_and_invokable() -> None:
    app = _FakeApp()
    selectable = {item["selectable_name"] for item in app.action_catalog()}

    assert "FINISHED" in selectable
    assert app.invoke_action_with_kwargs("FINISHED", {}) == "Finished action episode"
