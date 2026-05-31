from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest

from silisocs.environments.backends.base import BackendApp, SocialBackendApp, app_action
from silisocs.environments.backends.factory import create_backend_app
from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.resource_market.app import ResourceMarketApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.backends.virtual_space.app import VirtualSpaceApp
from silisocs.environments.gm.components.app_update import AppUpdateComponent
from silisocs.environments.gm.components.observe import AppObservationComponent


class _GenericTestApp(BackendApp):
    def __init__(self) -> None:
        super().__init__()
        self.initialized_with: dict[str, Any] | None = None
        self.observed_with: list[dict[str, Any]] = []
        self.updated_with: list[dict[str, Any]] = []

    def name(self) -> str:
        return "generic_test"

    def description(self) -> str:
        return "A generic test app"

    def initialize(self, agent_names: list[str], **kwargs: Any) -> None:
        self.initialized_with = {"agent_names": agent_names, **kwargs}

    def observe(self, actor_name: str, **kwargs: Any) -> str:
        self.observed_with.append({"actor_name": actor_name, **kwargs})
        return f"{actor_name} sees generic state"

    def update(self, *, step: int, agent_names: Sequence[str], context: Any | None = None) -> None:
        self.updated_with.append(
            {"step": step, "agent_names": list(agent_names), "context": context}
        )

    @app_action(selectable_name="WORK", description="Do work")
    def do_work(self, current_user: str, amount: int) -> str:
        return f"{current_user} worked {amount}"


def test_environment_app_keeps_action_catalog_generic() -> None:
    app = _GenericTestApp()

    catalog = app.action_catalog()
    selectable = {item["selectable_name"] for item in catalog}

    assert "WORK" in selectable
    assert "FINISHED" in selectable
    assert (
        app.invoke_action_with_kwargs(
            "WORK",
            {"current_user": "Alice", "amount": "3"},
        )
        == "Alice worked 3"
    )


def test_backend_app_is_domain_neutral() -> None:
    for method_name in (
        "setup_social_state",
        "get_timeline",
        "get_timeline_mode",
        "format_timeline_for_observation",
        "parse_and_resolve_action",
        "init_recsys",
        "update_recommendations",
    ):
        assert method_name not in BackendApp.__dict__


def test_social_backend_app_owns_social_surface() -> None:
    for method_name in (
        "setup_social_state",
        "get_timeline",
        "get_timeline_mode",
        "format_timeline_for_observation",
        "parse_and_resolve_action",
    ):
        assert method_name in SocialBackendApp.__dict__


def test_builtin_backend_classes_use_correct_base() -> None:
    assert issubclass(TwitterLikeApp, SocialBackendApp)
    assert issubclass(RedditLikeApp, SocialBackendApp)
    assert not issubclass(ResourceMarketApp, SocialBackendApp)
    assert not issubclass(VirtualSpaceApp, SocialBackendApp)
    assert issubclass(ResourceMarketApp, BackendApp)
    assert issubclass(VirtualSpaceApp, BackendApp)


def test_app_observation_component_delegates_to_environment_observe() -> None:
    app = _GenericTestApp()
    component = AppObservationComponent(
        model=object(),
        agent_names=("Alice",),
        backend=app,
        agent_flow_tags={"Alice": "market"},
        observation_params={"limit": 4},
    )

    result = component.make_observation("Alice")

    assert result == "Alice sees generic state"
    assert app.observed_with == [
        {
            "actor_name": "Alice",
            "step": 0,
            "flow_tag": "market",
            "limit": 4,
        }
    ]


def test_app_update_component_delegates_to_backend_update() -> None:
    class Agent:
        def __init__(self, name: str) -> None:
            self.name = name

    app = _GenericTestApp()
    component = AppUpdateComponent(backend=app)
    context = object()

    component.update(step=3, agents=[Agent("Alice"), Agent("Bob")], context=context)

    assert app.updated_with == [
        {
            "step": 3,
            "agent_names": ["Alice", "Bob"],
            "context": context,
        }
    ]


def test_custom_environment_app_rejects_unknown_params() -> None:
    with pytest.raises(ValueError, match="Unsupported config param"):
        create_backend_app(
            "custom",
            class_path="tests.test_environment_app_contracts._GenericTestApp",
            params={"unknown_param": True},
        )
