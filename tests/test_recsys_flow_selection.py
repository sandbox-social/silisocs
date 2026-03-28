from __future__ import annotations

from types import SimpleNamespace

from concordia.typing import entity as entity_lib

from mastodon_sim.environments.backends.reddit_like.engine import RedditLikePlatform
from mastodon_sim.environments.backends.twitter_like.engine import TwitterLikePlatform
from mastodon_sim.environments.gm.components.observe import TimelineMakeObservation
from mastodon_sim.environments.gm.components.recommend import RecommendationComponent
from mastodon_sim.runtime.config import ConfigStore


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


def test_timeline_observe_passes_flow_specific_recsys_type() -> None:
    app = _FakeApp()
    previous_config = ConfigStore._config
    ConfigStore._config = SimpleNamespace(sim=SimpleNamespace(timeline_posts=10))
    component = TimelineMakeObservation(
        model=object(),
        player_names=("alice",),
        sm_app=app,
        entity_flow_tags={"alice": "active"},
        timeline_mode="pure_recsys",
    )
    component.set_flow_field_values({"active": {"recsys_type": "twitter"}})

    action_spec = SimpleNamespace(
        output_type=entity_lib.OutputType.MAKE_OBSERVATION,
        call_to_action="alice",
    )

    try:
        component.pre_act(action_spec)
    finally:
        ConfigStore._config = previous_config

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


def test_recommendation_component_supports_entity_binding() -> None:
    component = RecommendationComponent(sm_app=object())
    entity = object()

    component.set_entity(entity)

    assert component.get_entity() is entity
