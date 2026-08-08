"""Catalog guards for previously-parked actions activated in the production pass.

These actions used to live as commented-out ``@app_action`` methods. They are now
live, so their absence from the backend action catalog is a regression. None of
these assertions require a live server — they only inspect the action catalog.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from silisocs.environments.backends.mastodon.apps import SocialNetworkApp
from silisocs.environments.backends.reddit_like.app import RedditLikeApp

_REDDIT_SUBREDDIT_ACTIONS = {
    "create_subreddit",
    "join_subreddit",
    "leave_subreddit",
    "get_subreddit_feed",
}

_MASTODON_ACTIVATED_ACTIONS = {
    "post_status",
    "get_public_timeline",
    "get_user_timeline",
    "block_user",
    "unblock_user",
    "mute_account",
    "unmute_account",
    "delete_posts",
    "send_direct_message",
}


def test_reddit_app_exposes_activated_subreddit_actions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        app = RedditLikeApp(db_path=str(Path(tmp) / "rd.db"))
        try:
            names = {action.name for action in app.actions()}
            assert names >= _REDDIT_SUBREDDIT_ACTIONS
        finally:
            app.shutdown()


def test_reddit_subreddit_actions_work_through_app_invoke_path() -> None:
    """End-to-end: the activated subreddit actions run via the @app_action dispatch."""
    with tempfile.TemporaryDirectory() as tmp:
        app = RedditLikeApp(db_path=str(Path(tmp) / "rd.db"))
        try:
            app.setup_social_state(agent_names=["Alice Smith", "Bob Stone"], following_graph={})

            created = app.invoke_action_with_kwargs(
                "create_subreddit",
                {"agent_name": "Alice Smith", "subreddit_name": "books", "description": "club"},
            )
            assert "books" in created and "Error" not in created

            joined = app.invoke_action_with_kwargs(
                "join_subreddit", {"agent_name": "Bob Stone", "subreddit_name": "books"}
            )
            assert "books" in joined and "Error" not in joined

            feed = app.invoke_action_with_kwargs(
                "get_subreddit_feed",
                {"agent_name": "Alice Smith", "subreddit_name": "books", "limit": 5},
            )
            assert "Error" not in feed

            left = app.invoke_action_with_kwargs(
                "leave_subreddit", {"agent_name": "Bob Stone", "subreddit_name": "books"}
            )
            assert "books" in left and "Error" not in left
        finally:
            app.shutdown()


def test_mastodon_app_exposes_activated_actions() -> None:
    app = SocialNetworkApp(perform_operations=False)
    names = {action.name for action in app.actions()}
    assert names >= _MASTODON_ACTIVATED_ACTIONS


def test_mastodon_does_not_expose_superseded_post_media_toot() -> None:
    """post_media_toot stays inactive — superseded by the media-capable post_toot."""
    app = SocialNetworkApp(perform_operations=False)
    names = {action.name for action in app.actions()}
    assert "post_media_toot" not in names
    assert "post_toot" in names


def test_activated_actions_have_well_formed_descriptors() -> None:
    """Every activated action must build a valid descriptor (docstring + params)."""
    app = SocialNetworkApp(perform_operations=False)
    by_name = {action.name: action for action in app.actions()}
    for name in _MASTODON_ACTIVATED_ACTIONS:
        action = by_name[name]
        assert action.description.strip(), f"{name} has no description"
        # agent_name is runtime-owned and must not be agent-visible.
        visible = {p.name for p in action.agent_visible_parameters}
        assert "agent_name" not in visible, f"{name} leaks agent_name to the agent"
