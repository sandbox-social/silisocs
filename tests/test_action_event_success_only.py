"""Committed-only contract for the canonical action-event log.

An ``action_events.jsonl`` row means a committed state change (or a successful
deliberate read): failed actions and idempotent no-ops (already-liked, second
mute) must leave NO row, because replay-based checkpoint restore re-executes
logged rows and eval metrics count them as ground truth. Errors stay observable
via the returned message and the ``backend_action_errors`` telemetry counter.

Also covers the replay-mapper completeness companion: every state-changing label
the twitter backend logs maps to a replayable action (previously ``unlike``,
``quote_repost``, ``update_profile`` etc. raised ``ValueError`` at replay time).
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.runtime.checkpointing.replay_mappers import microblog_event_to_replay_action


class _RecordingLogger:
    """Minimal action_logger stand-in: collects rows, exposes episode_idx."""

    def __init__(self) -> None:
        self.episode_idx: int | None = 0
        self.rows: list[dict[str, Any]] = []

    def log(self, log_data: Any) -> None:
        self.rows.append(dict(log_data))

    def labels(self) -> list[str]:
        return [str(row.get("label")) for row in self.rows]


@pytest.fixture
def twitter(tmp_path: Any) -> Any:
    logger = _RecordingLogger()
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"), action_logger=logger)
    app.setup_social_state(agent_names=["Alice", "Bob"], following_graph={})
    logger.rows.clear()  # drop init_* rows; tests assert on agent actions only
    yield app, logger
    app.shutdown()


@pytest.fixture
def reddit(tmp_path: Any) -> Any:
    logger = _RecordingLogger()
    app = RedditLikeApp(db_path=str(tmp_path / "rd.db"), action_logger=logger)
    app.setup_social_state(agent_names=["Alice", "Bob"])
    logger.rows.clear()
    yield app, logger
    app.shutdown()


# --------------------------------------------------------------------------- #
# Twitter-like: failures leave no row
# --------------------------------------------------------------------------- #


def test_twitter_failed_actions_log_no_event(twitter: Any) -> None:
    app, logger = twitter
    # Nonexistent post ids -> the platform raises; the app catches it, returns an
    # error message, and must NOT log an action event.
    assert "Error" in app.like_tweet("Alice", 999)
    assert "Error" in app.repost_tweet("Alice", 999)
    assert "Error" in app.quote_repost_tweet("Alice", 999, "hot take")
    # Unliking a never-liked post is an idempotent no-op (the platform clamps it),
    # not an error -> a friendly message but still NO committed row.
    assert "had not liked" in app.unlike_tweet("Alice", 999)
    assert logger.rows == []


def test_twitter_follow_noops_log_no_event(twitter: Any) -> None:
    app, logger = twitter
    app.follow_user("Alice", "Bob")  # a real, committed follow
    assert logger.labels() == ["follow"]
    logger.rows.clear()
    # Already-following and self-follow both return False (no state change) -> no row.
    assert "already follows" in app.follow_user("Alice", "Bob")
    assert "already follows" in app.follow_user("Alice", "Alice")
    assert logger.rows == []


def test_twitter_successful_actions_log_exactly_one_event(twitter: Any) -> None:
    app, logger = twitter
    app.create_tweet("Alice", "hello world")
    app.like_tweet("Bob", 1)
    app.repost_tweet("Bob", 1)
    app.follow_user("Bob", "Alice")
    assert logger.labels() == ["post", "like", "repost", "follow"]


def test_twitter_idempotent_noops_log_no_event(twitter: Any) -> None:
    app, logger = twitter
    app.create_tweet("Alice", "hello world")
    app.like_tweet("Bob", 1)
    logger.rows.clear()
    # Second like is an "already liked" no-op -> no state change, no row.
    assert "already liked" in app.like_tweet("Bob", 1)
    # Second mute / unmute-of-unmuted / report duplicates return False -> no row.
    app.mute_user("Bob", "Alice")
    logger.rows.clear()
    assert "could not mute" in app.mute_user("Bob", "Alice")
    app.unmute_user("Bob", "Alice")
    logger.rows.clear()
    assert "could not unmute" in app.unmute_user("Bob", "Alice")
    # Undoing a dislike that doesn't exist is a no-op.
    assert "could not remove dislike" in app.undo_dislike_post("Bob", 1)
    assert logger.rows == []


def test_twitter_failed_read_logs_no_event(tmp_path: Any) -> None:
    logger = _RecordingLogger()
    app = TwitterLikeApp(db_path=str(tmp_path / "tw.db"), action_logger=logger)
    app.setup_social_state(agent_names=["Alice"], following_graph={})
    logger.rows.clear()

    class _Boom:
        def __getattr__(self, name: str) -> Any:
            raise RuntimeError("db down")

    app._platform = _Boom()  # type: ignore[assignment]  # simulate a read-path failure
    assert "Error" in app.get_trending_posts("Alice")
    assert logger.rows == []


# --------------------------------------------------------------------------- #
# Reddit-like: failures leave no row
# --------------------------------------------------------------------------- #


def test_reddit_failed_actions_log_no_event(reddit: Any) -> None:
    app, logger = reddit
    assert "Error" in app.upvote("Alice", 999, "post")
    assert "Error" in app.downvote("Alice", 999, "post")
    assert logger.rows == []


def test_reddit_successful_and_noop_actions(reddit: Any) -> None:
    app, logger = reddit
    app.create_reddit_post("Alice", "general", "Hello", "hello reddit")
    app.upvote("Bob", 1, "post")
    assert logger.labels() == ["post", "upvote"]
    logger.rows.clear()
    # First mute commits; the duplicate mute is a no-op (returns False) -> no row.
    app.mute_user("Bob", "Alice")
    assert logger.labels() == ["mute_user"]
    logger.rows.clear()
    assert "could not mute" in app.mute_user("Bob", "Alice")
    assert logger.rows == []


def test_reddit_revote_noop_logs_no_event(reddit: Any) -> None:
    app, logger = reddit
    app.create_reddit_post("Alice", "general", "Hello", "hello reddit")
    app.upvote("Bob", 1, "post")  # first upvote commits
    assert logger.labels() == ["post", "upvote"]
    logger.rows.clear()
    # Re-affirming the same vote changes nothing (engine returns False) -> no row.
    assert "had already upvoted" in app.upvote("Bob", 1, "post")
    assert logger.rows == []


def test_reddit_noop_family_logs_no_event(reddit: Any) -> None:
    """The False-returning extended-action family leaves no row on a no-op."""
    app, logger = reddit
    app.create_reddit_post("Alice", "general", "Hello", "hello reddit")
    logger.rows.clear()
    # None of these changed state (never upvoted/disliked; not muted) -> no rows.
    assert "could not" in app.unlike_post("Bob", 1).lower()
    assert "could not" in app.undo_dislike_post("Bob", 1).lower()
    assert "could not" in app.unmute_user("Bob", "Alice").lower()
    assert logger.rows == []
    # A dislike commits (state change) then a redundant re-dislike is a no-op.
    app.dislike_post("Bob", 1)
    assert logger.labels() == ["dislike_post"]
    logger.rows.clear()
    assert "could not" in app.dislike_post("Bob", 1).lower()
    assert logger.rows == []


# --------------------------------------------------------------------------- #
# Replay-mapper completeness: every logged state-changing twitter label maps
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("label", "data", "expected_action", "expected_args"),
    [
        ("unlike", {"post_id": "3"}, "unlike_tweet", {"post_id": "3"}),
        (
            "quote_repost",
            {"post_id": "3", "content": "take"},
            "quote_repost_tweet",
            {"post_id": "3", "status": "take"},
        ),
        ("update_profile", {"new_bio": "bio text"}, "update_profile", {"bio": "bio text"}),
        ("unlike_post", {"post_id": "3"}, "unlike_post", {"post_id": "3"}),
        ("dislike_post", {"post_id": "3"}, "dislike_post", {"post_id": "3"}),
        ("undo_dislike_post", {"post_id": "3"}, "undo_dislike_post", {"post_id": "3"}),
        ("mute_user", {"target_user": "Bob"}, "mute_user", {"target_user": "Bob"}),
        ("unmute_user", {"target_user": "Bob"}, "unmute_user", {"target_user": "Bob"}),
        (
            "report_post",
            {"post_id": "3", "reason": "spam"},
            "report_post",
            {"post_id": "3", "reason": "spam"},
        ),
    ],
)
def test_microblog_mapper_covers_state_changing_labels(
    label: str, data: dict[str, Any], expected_action: str, expected_args: dict[str, Any]
) -> None:
    action = microblog_event_to_replay_action(label, data)
    assert action is not None
    call = action.tool_calls[0]
    assert call.name == expected_action
    assert dict(call.arguments) == expected_args


def test_microblog_mapper_still_rejects_unknown_labels() -> None:
    with pytest.raises(ValueError, match="Unknown microblog action event label"):
        microblog_event_to_replay_action("definitely_not_a_label", {})
