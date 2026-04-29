"""Initialization logging coverage for local social media apps."""

from __future__ import annotations

from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp


class _DummyActionLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def test_twitter_like_initialize_emits_action_events(tmp_path) -> None:
    logger = _DummyActionLogger()
    app = TwitterLikeApp(
        action_logger=logger,
        db_path=str(tmp_path / "twitter_like_test.db"),
    )
    try:
        app.initialize(
            agent_names=["Alice Smith", "Bob Jones"],
            sim_roles={"Alice Smith": "voter", "Bob Jones": "voter"},
            seed_posts={"Alice Smith": "First tweet from Alice"},
            social_network={"network_type": "barabasi_albert", "barabasi_albert_m": 1},
        )
    finally:
        app.shutdown()

    labels = [event.get("label") for event in logger.events]
    assert "init_create_user" in labels
    assert "post" in labels
    assert "initialize" in labels


def test_reddit_like_initialize_emits_action_events(tmp_path) -> None:
    logger = _DummyActionLogger()
    app = RedditLikeApp(
        action_logger=logger,
        db_path=str(tmp_path / "reddit_like_test.db"),
    )
    try:
        app.initialize(
            agent_names=["Alice Smith", "Bob Jones"],
            sim_roles={"Alice Smith": "voter", "Bob Jones": "voter"},
            seed_posts={"Alice Smith": "First post from Alice"},
            social_network={
                "subreddits": [
                    {"name": "general", "description": "General discussion", "roles": "all"}
                ],
                "default_subreddit": "general",
            },
        )
    finally:
        app.shutdown()

    labels = [event.get("label") for event in logger.events]
    assert "init_create_subreddit" in labels
    assert "init_create_user" in labels
    assert "initialize" in labels
