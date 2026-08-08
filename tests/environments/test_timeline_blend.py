"""Fast unit tests for the shared timeline dispatch + blend in SqliteSocialEngineBase.

The strategy dispatch and the recsys/follower blend are identical across the
twitter/reddit engines, so they now live on the base. These tests stub out the DB
(``get_feed``/``get_recommendations``) to cover the dispatch and dedup logic in
milliseconds, complementing the slower real-backend feed-contract suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from silisocs.environments.backends.social.sqlite_engine import SqliteSocialEngineBase


class _StubEngine(SqliteSocialEngineBase):
    """Minimal engine that returns canned posts without a database."""

    def __init__(self, recs: list[dict], feed_posts: list[dict], follower_feed: str = "home"):
        # Deliberately skip SqliteSocialEngineBase.__init__ (no DB/threads needed).
        self._recs = recs
        self._feed_posts = feed_posts
        self._follower_feed_strategy = follower_feed
        self.requested_feed: str | None = None

    def get_recommendations(self, username, limit=10, recsys_type=None):  # type: ignore[override]
        return self._recs[:limit]

    def get_feed(self, feed_type, username=None, **kwargs: Any):  # type: ignore[override]
        self.requested_feed = feed_type
        return {"posts": self._feed_posts[: kwargs.get("limit", 10)]}


def test_pure_recsys_returns_recommendations_only():
    engine = _StubEngine(recs=[{"id": 1}, {"id": 2}], feed_posts=[{"id": 9}])
    # get_timeline tags each post with its exposure source (recsys, no type here).
    assert engine.get_timeline("pure_recsys", "alice", limit=5) == [
        {"id": 1, "source": "recsys"},
        {"id": 2, "source": "recsys"},
    ]


def test_follower_chronological_uses_the_configured_feed():
    engine = _StubEngine(recs=[], feed_posts=[{"id": 7}], follower_feed="chronological_home")
    assert engine.get_timeline("follower_chronological", "alice") == [
        {"id": 7, "source": "follower"}
    ]
    assert engine.requested_feed == "chronological_home"


def test_hybrid_blends_recsys_first_then_follower_deduped():
    engine = _StubEngine(recs=[{"id": 1}, {"id": 2}], feed_posts=[{"id": 2}, {"id": 3}])
    # Recsys posts lead, the shared post (id 2) is not duplicated (keeps its recsys
    # source), follower posts follow.
    assert engine.get_timeline("hybrid_recsys_follower", "alice", limit=10) == [
        {"id": 1, "source": "recsys"},
        {"id": 2, "source": "recsys"},
        {"id": 3, "source": "follower"},
    ]


def test_hybrid_respects_post_id_fallback_key():
    # Posts may carry "post_id" instead of "id"; dedup keys off whichever is present.
    engine = _StubEngine(recs=[{"post_id": 5}], feed_posts=[{"post_id": 5}, {"post_id": 6}])
    assert engine.get_timeline("hybrid_recsys_follower", "alice", limit=10) == [
        {"post_id": 5, "source": "recsys"},
        {"post_id": 6, "source": "follower"},
    ]


def test_unknown_strategy_raises():
    engine = _StubEngine(recs=[], feed_posts=[])
    with pytest.raises(ValueError, match="Unknown timeline strategy"):
        engine.get_timeline("does_not_exist", "alice")
