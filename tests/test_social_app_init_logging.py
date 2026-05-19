"""Initialization logging coverage for local social media apps."""

from __future__ import annotations

from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

from silisocs.agents.base_agent import Agent
from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent
from silisocs.initialization.context import InitializationContext
from silisocs.initialization.game_masters import SocialMediaGameMasterInitializer
from silisocs.initialization.simulation import SeedPostsSimulationInitializer
from silisocs.initialization.simulation.seed_posts import SeedPostProvider


class _SeedProvider(SeedPostProvider):
    def __init__(self, seed_posts: dict[str, str]) -> None:
        self._seed_posts = dict(seed_posts)

    def get_seed_posts(self, **kwargs):
        del kwargs
        return dict(self._seed_posts)


class _DummyActionLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def _initialize_social_app(app, *, social_network: dict, seed_posts: dict[str, str]) -> None:
    agents = [
        SimpleNamespace(name="Alice Smith"),
        SimpleNamespace(name="Bob Jones"),
    ]
    platform_type = "reddit_like" if isinstance(app, RedditLikeApp) else "twitter_like"
    app.platform_type = platform_type
    resolver = ToolCallingResolveComponent(sm_app=app)
    game_master = SimpleNamespace(
        app=app,
        action_output_mode="tool_calling",
        resolve_action=resolver.resolve_action,
    )
    context = InitializationContext(
        sim_roles={"Alice Smith": "voter", "Bob Jones": "voter"},
        social_network=social_network,
    )
    initializer = SocialMediaGameMasterInitializer()
    typed_agents = cast(Sequence[Agent], agents)
    initializer.initialize(agents=typed_agents, game_master=game_master, context=context)
    SeedPostsSimulationInitializer(seed_post_provider=_SeedProvider(seed_posts)).initialize(
        agents=typed_agents,
        game_masters=[game_master],
        model=cast(Any, object()),
        context=context,
    )


def test_twitter_like_initializer_emits_action_events(tmp_path) -> None:
    logger = _DummyActionLogger()
    app = TwitterLikeApp(
        action_logger=logger,
        db_path=str(tmp_path / "twitter_like_test.db"),
    )
    try:
        _initialize_social_app(
            app,
            social_network={"network_type": "barabasi_albert", "barabasi_albert_m": 1},
            seed_posts={"Alice Smith": "First tweet from Alice"},
        )
    finally:
        app.shutdown()

    labels = [event.get("label") for event in logger.events]
    assert "init_create_user" in labels
    assert "post" in labels
    assert "initialize" in labels


def test_reddit_like_initializer_emits_action_events(tmp_path) -> None:
    logger = _DummyActionLogger()
    app = RedditLikeApp(
        action_logger=logger,
        db_path=str(tmp_path / "reddit_like_test.db"),
    )
    try:
        _initialize_social_app(
            app,
            social_network={
                "subreddits": [
                    {"name": "general", "description": "General discussion", "roles": "all"}
                ],
                "default_subreddit": "general",
            },
            seed_posts={"Alice Smith": "First post from Alice"},
        )
    finally:
        app.shutdown()

    labels = [event.get("label") for event in logger.events]
    assert "init_create_subreddit" in labels
    assert "init_create_user" in labels
    assert "initialize" in labels
