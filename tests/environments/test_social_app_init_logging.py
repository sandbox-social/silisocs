"""Initialization logging coverage for local social media apps."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from replications.echo_chambers.components.app import EchoChamberSocialApp

from silisocs.agents.base_agent import Agent
from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.gm.components.resolve import ToolCallingResolveComponent
from silisocs.environments.gm.context import GameMasterContext
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


def _initialize_social_app(app, *, graph_config: dict, seed_posts: dict[str, str]) -> None:
    agents = [
        SimpleNamespace(name="Alice Smith"),
        SimpleNamespace(name="Bob Jones"),
    ]
    backend_type = "reddit_like" if isinstance(app, RedditLikeApp) else "twitter_like"
    resolver = ToolCallingResolveComponent(backend=app)
    game_master = SimpleNamespace(
        backend=app,
        backend_type=backend_type,
        owned_flows=("default",),
        resolve_action=resolver.resolve_action,
    )
    context = InitializationContext(
        sim_roles={"Alice Smith": "voter", "Bob Jones": "voter"},
    )
    initializer = SocialMediaGameMasterInitializer(graph=graph_config)
    typed_agents = cast(Sequence[Agent], agents)
    gm_context = GameMasterContext(
        gm_name=f"{backend_type}_gm",
        backend=app,
        agents=typed_agents,
        agent_names=tuple(agent.name for agent in agents),
        agent_flow_tags={"Alice Smith": "default", "Bob Jones": "default"},
    )
    initializer.initialize(agents=typed_agents, gm_context=gm_context, context=context)
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
            graph_config={"network_type": "barabasi_albert", "barabasi_albert_m": 1},
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
            graph_config={
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


def test_echo_loose_social_setup_seeds_initial_opinion_posts(tmp_path) -> None:
    logger = _DummyActionLogger()
    root = Path("replications/echo_chambers/input")
    app = EchoChamberSocialApp(
        action_logger=logger,
        db_path=str(tmp_path / "echo_loose_social.db"),
        agent_records_path=str(root / "agent_records.json"),
        belief_keywords_path=str(root / "belief_keywords.json"),
        opinions_path=str(root / "opinions.json"),
        network_path=str(root / "networks/scale_free_network_num_agents_50_seed_50.json"),
        seed_initial_opinion_posts=True,
    )
    try:
        state = app._build_world()
        agents = [SimpleNamespace(name=name) for name in state.agent_names]
        context = InitializationContext(sim_roles=dict.fromkeys(state.agent_names, "echo_user"))
        gm_context = GameMasterContext(
            gm_name="echo_gm",
            backend=app,
            agents=cast(Sequence[Agent], agents),
            agent_names=tuple(state.agent_names),
            agent_flow_tags=dict.fromkeys(state.agent_names, "default"),
        )
        initializer = SocialMediaGameMasterInitializer(
            graph={
                "network_type": "predefined",
                "predefined_graph": {},
                "predefined_graph_path": str(
                    root / "networks/scale_free_network_num_agents_50_seed_50.json"
                ),
            }
        )
        initializer.initialize(
            agents=cast(Sequence[Agent], agents),
            gm_context=gm_context,
            context=context,
        )
    finally:
        app.shutdown()

    labels = [event.get("label") for event in logger.events]
    assert labels.count("post") == 50
    assert "echo_chamber_social_seed_posts" in labels
