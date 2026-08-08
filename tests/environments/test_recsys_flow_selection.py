from __future__ import annotations

import pytest

from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform
from silisocs.environments.gm.components.social_media.observe import TimelineMakeObservation
from silisocs.environments.gm.components.social_media.update import (
    SocialRecommendationUpdateComponent,
)


class _FakeApp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_timeline_mode(
        self,
        timeline_mode: str,
        user_name: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: dict,
    ) -> list[dict]:
        self.calls.append(
            {
                "timeline_mode": timeline_mode,
                "user_name": user_name,
                "limit": limit,
                "recsys_type": recsys_type,
                "timeline_config": timeline_config,
            }
        )
        return []

    def format_timeline_for_observation(self, timeline: list[dict]) -> str:
        return ""


def test_timeline_observe_passes_configured_recsys_type() -> None:
    app = _FakeApp()
    component = TimelineMakeObservation(
        model=object(),
        agent_names=("alice",),
        backend=app,
        agent_flow_tags={"alice": "active"},
        timeline_mode="pure_recsys",
        recsys_type="twitter",
    )

    component.make_observation("alice")

    assert app.calls
    assert app.calls[0]["recsys_type"] == "twitter"


def test_twitter_platform_filters_recommendations_by_recsys_type(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "twitter.db"), use_queue=False)

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("alice", "bio", 0.0),
        )
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("bob", "bio", 0.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (2, "twitter-post", 1.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (2, "reddit-post", 2.0),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (1, 1, "twitter"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (1, 2, "reddit"),
        )
        conn.commit()

    posts = platform.get_recommendations("alice", recsys_type="twitter")

    assert [post["content"] for post in posts] == ["twitter-post"]


def test_reddit_platform_filters_recommendations_by_recsys_type(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "reddit.db"), use_queue=False)

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("alice", "bio", 0.0),
        )
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("bob", "bio", 0.0),
        )
        conn.execute(
            "INSERT INTO subreddits (name, description, created_at) VALUES (?, ?, ?)",
            ("test", "desc", 0.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, subreddit_id, title, content, created_at, upvotes, downvotes, comment_count)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0)
            """,
            (2, 1, "twitter-title", "twitter-post", 1.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, subreddit_id, title, content, created_at, upvotes, downvotes, comment_count)
            VALUES (?, ?, ?, ?, ?, 0, 0, 0)
            """,
            (2, 1, "reddit-title", "reddit-post", 2.0),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (1, 1, "twitter"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (1, 2, "reddit"),
        )
        conn.commit()

    posts = platform.get_recommendations("alice", recsys_type="twitter")

    assert [post["content"] for post in posts] == ["twitter-post"]


def test_twitter_platform_rejects_reddit_algorithm(tmp_path) -> None:
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_invalid_algo.db"), use_queue=False
    )

    try:
        platform.init_recsys("reddit")
    except ValueError as exc:
        assert "Unsupported recsys_type" in str(exc)
        assert "twitter" in str(exc)
        return

    raise AssertionError("Expected ValueError for unsupported reddit algorithm on twitter_like")


def test_twitter_platform_supports_twitter_tfidf_algorithm(tmp_path) -> None:
    pytest.importorskip("sklearn", reason="TF-IDF recsys test requires recsys extra")
    platform = TwitterLikePlatform(db_path=str(tmp_path / "twitter_tfidf.db"), use_queue=False)

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("alice", "interested in climate policy", 0.0),
        )
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("bob", "general news", 0.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (2, "climate change and policy update", 1.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (2, "sports highlights", 2.0),
        )
        conn.commit()

    platform.init_recsys("twitter_tfidf")
    platform.update_recommendations(max_posts=2)
    posts = platform.get_recommendations("alice", recsys_type="twitter_tfidf")

    assert posts
    assert {post["content"] for post in posts} == {
        "climate change and policy update",
        "sports highlights",
    }


def test_twitter_tfidf_recommends_expected_top_post_and_excludes_self(tmp_path) -> None:
    pytest.importorskip("sklearn", reason="TF-IDF recsys test requires recsys extra")
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_tfidf_correctness.db"),
        use_queue=False,
    )

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("alice", "climate policy and carbon tax", 0.0),
        )
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("bob", "general news", 0.0),
        )
        conn.execute(
            "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
            ("cara", "sports fan", 0.0),
        )

        # Alice's own highly similar post should never appear in her recommendations.
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (1, "climate policy and carbon tax explained", 1.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (2, "climate policy debate and carbon pricing", 2.0),
        )
        conn.execute(
            """
            INSERT INTO posts
            (user_id, content, created_at, type, likes_count, reposts_count, reply_count)
            VALUES (?, ?, ?, 'post', 0, 0, 0)
            """,
            (3, "football transfer rumors", 3.0),
        )
        conn.commit()

    platform.init_recsys("twitter_tfidf")
    platform.update_recommendations(max_posts=1)
    posts = platform.get_recommendations("alice", recsys_type="twitter_tfidf", limit=1)

    assert len(posts) == 1
    assert posts[0]["content"] == "climate policy debate and carbon pricing"
    assert posts[0]["username"] != "alice"


def test_reddit_platform_rejects_twitter_algorithm(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "reddit_invalid_algo.db"), use_queue=False)

    try:
        platform.init_recsys("twitter")
    except ValueError as exc:
        assert "Unsupported recsys_type" in str(exc)
        assert "reddit" in str(exc)
        return

    raise AssertionError("Expected ValueError for unsupported twitter algorithm on reddit_like")


def test_social_recommendation_has_no_native_owner_binding() -> None:
    component = SocialRecommendationUpdateComponent(backend=object())

    assert not hasattr(component, "set_entity")
    assert not hasattr(component, "get_entity")


# --------------------------------------------------------------------------- #
# Loud recsys: config errors raise, per-step update failures are counted
# --------------------------------------------------------------------------- #


class _RecsysBackend:
    """Minimal backend exposing the recsys hooks the update component reconciles."""

    def __init__(
        self, *, init_error: Exception | None = None, update_error: Exception | None = None
    ):
        self._init_error = init_error
        self._update_error = update_error
        self.action_logger = None
        self.live: set[str] = set()
        self.updates = 0

    def recsys_active_types(self) -> set[str]:
        return set(self.live)

    def init_recsys(self, recsys_type: str, **_: object) -> None:
        if self._init_error is not None:
            raise self._init_error
        self.live.add(recsys_type)

    def update_recommendations(self, active_user_ids=None, max_posts: int = 10) -> int:
        del active_user_ids, max_posts
        if self._update_error is not None:
            raise self._update_error
        self.updates += 1
        return 0


def test_recsys_init_failure_for_configured_type_raises() -> None:
    """An explicitly configured recsys that cannot be initialized is a CONFIG error.

    It used to be swallowed by a blanket handler, so the whole scenario ran with
    no recommender at all — the one thing it asked for.
    """
    backend = _RecsysBackend(init_error=RuntimeError("sentence-transformers not installed"))
    component = SocialRecommendationUpdateComponent(
        backend=backend,
        backend_type="twitter_like",
        default_recsys_type="twitter",
    )

    with pytest.raises(RuntimeError, match="sentence-transformers"):
        component.update_recommendations()


def test_recsys_update_failure_increments_health_counter() -> None:
    """ONE failed scheduled refresh is survivable — so it is COUNTED, not raised."""
    from silisocs.evaluations.vocabulary import HEALTH_COUNTERS
    from silisocs.runtime.telemetry.collector import SimMetricsCollector

    assert "recsys_update_failures" in HEALTH_COUNTERS

    backend = _RecsysBackend(update_error=RuntimeError("db locked"))
    component = SocialRecommendationUpdateComponent(
        backend=backend,
        backend_type="twitter_like",
        default_recsys_type="twitter",
    )

    SimMetricsCollector.reset()
    component.update_recommendations()  # survives
    assert SimMetricsCollector.get().counter("recsys_update_failures") == 1


def test_twitter_engine_update_recommendations_propagates(tmp_path) -> None:
    """No -1 sentinel: a failed update raises so the component can count it."""
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw_fail.db"), use_queue=False)
    platform.create_users([("alice", "a"), ("bob", "b")])
    platform.create_post("bob", "hello")
    platform.init_recsys("twitter_tfidf")

    def _boom(*_a: object, **_k: object) -> dict:
        raise RuntimeError("recsys exploded")

    platform._rec_tfidf = _boom  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="recsys exploded"):
        platform.update_recommendations(max_posts=1)
