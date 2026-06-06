from __future__ import annotations

import sys
import types

import pytest

from silisocs.environments.backends.reddit_like.app import RedditLikeApp
from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.twitter_like.app import TwitterLikeApp
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform


def test_twitter_timeline_oracle_follower_chronological_matches_expected_posts(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "twitter_feed.db"), use_queue=False)

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_user("charlie")
    platform.follow("alice", "bob")

    platform.create_post("bob", "bob-post-1")
    platform.create_post("charlie", "charlie-post-1")
    platform.create_post("bob", "bob-post-2")

    timeline = platform.get_timeline("follower_chronological", "alice", limit=10)
    contents = [post["content"] for post in timeline]

    assert contents == ["bob-post-2", "bob-post-1"]


def test_twitter_like_app_checkpoint_state_restores_sqlite_backend(tmp_path) -> None:
    app = TwitterLikeApp(db_path=str(tmp_path / "twitter_source.db"))
    app.setup_social_state(agent_names=["Alice Smith", "Bob Stone"], following_graph={})
    app.create_tweet(agent_name="Alice Smith", status="checkpoint tweet")

    restored = TwitterLikeApp(db_path=str(tmp_path / "twitter_restored.db"))
    restored.set_state(app.get_state())

    timeline = restored._platform.get_feed("firehose", limit=10)["posts"]
    assert [post["content"] for post in timeline] == ["checkpoint tweet"]
    assert restored.create_tweet(agent_name="Alice Smith", status="after restore").startswith(
        "Alice Smith posted a tweet"
    )
    app.shutdown()
    restored.shutdown()


def test_twitter_timeline_oracle_pure_recsys_twitter(tmp_path) -> None:
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_recsys_twitter.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    first_post_id = platform.create_post("bob", "twitter-recsys-post")
    second_post_id = platform.create_post("bob", "twhin-recsys-post")

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, first_post_id, "twitter"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, second_post_id, "twhin"),
        )
        conn.commit()

    timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="twitter")
    contents = [post["content"] for post in timeline]

    assert contents == ["twitter-recsys-post"]


def test_twitter_timeline_oracle_pure_recsys_twhin(tmp_path) -> None:
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_recsys_twhin.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    first_post_id = platform.create_post("bob", "twitter-recsys-post")
    second_post_id = platform.create_post("bob", "twhin-recsys-post")

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, first_post_id, "twitter"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, second_post_id, "twhin"),
        )
        conn.commit()

    timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="twhin")
    contents = [post["content"] for post in timeline]

    assert contents == ["twhin-recsys-post"]


def test_reddit_timeline_oracle_follower_chronological_matches_expected_posts(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "reddit_feed.db"), use_queue=False)

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("general", "General discussion")
    platform.create_subreddit("sports", "Sports discussion")

    platform.join_subreddit("alice", "general")
    platform.join_subreddit("bob", "general")

    platform.create_post("bob", "general", "gen-1", "general-post-1")
    platform.create_post("bob", "sports", "sports-1", "sports-post-1")
    platform.create_post("bob", "general", "gen-2", "general-post-2")

    timeline = platform.get_timeline("follower_chronological", "alice", limit=10)
    contents = [post["content"] for post in timeline]

    assert contents == ["general-post-2", "general-post-1"]


def test_reddit_like_app_checkpoint_state_restores_sqlite_backend(tmp_path) -> None:
    app = RedditLikeApp(db_path=str(tmp_path / "reddit_source.db"))
    app.setup_social_state(
        agent_names=["Alice Smith", "Bob Stone"],
        graph_config={"subreddits": [{"name": "general", "roles": "all"}]},
    )
    app.create_reddit_post(
        agent_name="Alice Smith",
        subreddit="general",
        title="Checkpoint",
        content="checkpoint post",
    )

    restored = RedditLikeApp(db_path=str(tmp_path / "reddit_restored.db"))
    restored.set_state(app.get_state())

    timeline = restored._platform.get_feed("home", username="bobstone", limit=10)["posts"]
    assert [post["content"] for post in timeline] == ["checkpoint post"]
    assert restored.create_reddit_post(
        agent_name="Alice Smith",
        subreddit="general",
        title="After",
        content="after restore",
    ).startswith("Alice Smith posted in r/general")
    app.shutdown()
    restored.shutdown()


def test_reddit_timeline_oracle_pure_recsys_reddit(tmp_path) -> None:
    platform = RedditLikePlatform(
        db_path=str(tmp_path / "reddit_recsys_reddit.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("general", "General discussion")

    first_post_id = platform.create_post("bob", "general", "rec-1", "reddit-recsys-post")
    second_post_id = platform.create_post("bob", "general", "rec-2", "twhin-recsys-post")

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, first_post_id, "reddit"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, second_post_id, "twhin"),
        )
        conn.commit()

    timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="reddit")
    contents = [post["content"] for post in timeline]

    assert contents == ["reddit-recsys-post"]


def test_reddit_timeline_oracle_pure_recsys_twhin(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "reddit_recsys_twhin.db"), use_queue=False)

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("general", "General discussion")

    first_post_id = platform.create_post("bob", "general", "rec-1", "reddit-recsys-post")
    second_post_id = platform.create_post("bob", "general", "rec-2", "twhin-recsys-post")

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    with platform.get_connection() as conn:
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, first_post_id, "reddit"),
        )
        conn.execute(
            "INSERT INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
            (alice_id, second_post_id, "twhin"),
        )
        conn.commit()

    timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="twhin")
    contents = [post["content"] for post in timeline]

    assert contents == ["twhin-recsys-post"]


def test_reddit_recsys_update_computes_and_persists_recommendations(tmp_path) -> None:
    platform = RedditLikePlatform(
        db_path=str(tmp_path / "reddit_recsys_compute.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_user("carol")
    platform.create_subreddit("general", "General discussion")

    low_post_id = platform.create_post("bob", "general", "low", "low-score-post")
    top_post_id = platform.create_post("bob", "general", "top", "top-score-post")
    mid_post_id = platform.create_post("carol", "general", "mid", "mid-score-post")
    own_post_id = platform.create_post("alice", "general", "self", "self-post")

    # Keep timestamps equal so ranking is dominated by vote score in hot-score.
    with platform.get_connection() as conn:
        conn.execute(
            "UPDATE posts SET upvotes = ?, downvotes = ?, created_at = ? WHERE id = ?",
            (1, 0, 1000.0, low_post_id),
        )
        conn.execute(
            "UPDATE posts SET upvotes = ?, downvotes = ?, created_at = ? WHERE id = ?",
            (12, 0, 1000.0, top_post_id),
        )
        conn.execute(
            "UPDATE posts SET upvotes = ?, downvotes = ?, created_at = ? WHERE id = ?",
            (6, 0, 1000.0, mid_post_id),
        )
        conn.execute(
            "UPDATE posts SET upvotes = ?, downvotes = ?, created_at = ? WHERE id = ?",
            (999, 0, 1000.0, own_post_id),
        )
        conn.commit()

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    platform.init_recsys("reddit")
    platform.update_recommendations(active_user_ids=[alice_id], max_posts=2)

    with platform.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT post_id, recsys_type
            FROM recommendations
            WHERE user_id = ?
            """,
            (alice_id,),
        ).fetchall()

    assert rows
    assert all(row["recsys_type"] == "reddit" for row in rows)
    rec_post_ids = {int(row["post_id"]) for row in rows}

    # Alice should not be recommended her own post and should receive the top 2 scored foreign posts.
    assert own_post_id not in rec_post_ids
    assert rec_post_ids == {top_post_id, mid_post_id}

    rec_timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="reddit")
    rec_contents = {post["content"] for post in rec_timeline}
    assert rec_contents == {"top-score-post", "mid-score-post"}


def test_reddit_twhin_update_computes_and_persists_recommendations(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch", reason="TWHIN embedding recsys test requires recsys extra")
    platform = RedditLikePlatform(
        db_path=str(tmp_path / "reddit_twhin_compute.db"), use_queue=False
    )

    platform.create_user("alice", bio="alice-bio")
    platform.create_user("bob", bio="bob-bio")
    platform.create_user("carol", bio="carol-bio")
    platform.create_subreddit("general", "General discussion")

    low_post_id = platform.create_post("bob", "general", "low", "low-embed-post")
    top_post_id = platform.create_post("bob", "general", "top", "top-embed-post")
    mid_post_id = platform.create_post("carol", "general", "mid", "mid-embed-post")
    own_post_id = platform.create_post("alice", "general", "self", "self-embed-post")

    class _FakeSentenceTransformer:
        def __init__(self, model_name: str):
            self.model_name = model_name

        def encode(self, texts, batch_size: int = 32, convert_to_tensor: bool = True):
            del batch_size, convert_to_tensor
            import torch

            vectors = {
                "alice-bio": [1.0, 0.0],
                "top-embed-post": [0.9, 0.1],
                "mid-embed-post": [0.6, 0.8],
                "low-embed-post": [0.0, 1.0],
                "self-embed-post": [1.0, 0.0],
            }

            def _vec(text: str) -> list[float]:
                return vectors.get(str(text), [0.0, 0.0])

            if isinstance(texts, str):
                return torch.tensor(_vec(texts), dtype=torch.float32)
            return torch.tensor([_vec(text) for text in texts], dtype=torch.float32)

    fake_st_module = types.SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer)
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_st_module)

    alice_id = platform.get_user_id("alice")
    assert alice_id is not None

    platform.init_recsys("twhin")
    platform.update_recommendations(active_user_ids=[alice_id], max_posts=2)

    with platform.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT post_id, recsys_type
            FROM recommendations
            WHERE user_id = ?
            """,
            (alice_id,),
        ).fetchall()

    assert rows
    assert len(rows) == 2
    assert all(row["recsys_type"] == "twhin" for row in rows)
    rec_post_ids = {int(row["post_id"]) for row in rows}

    # Embedding ranking for alice-bio should pick top then mid, excluding alice's own post.
    assert own_post_id not in rec_post_ids
    assert rec_post_ids == {top_post_id, mid_post_id}

    rec_timeline = platform.get_timeline("pure_recsys", "alice", limit=10, recsys_type="twhin")
    rec_contents = {post["content"] for post in rec_timeline}
    assert rec_contents == {"top-embed-post", "mid-embed-post"}


def test_twitter_unknown_timeline_mode_fails_loudly(tmp_path) -> None:
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_default_timeline.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.follow("alice", "bob")
    platform.create_post("bob", "fallback-post")

    with pytest.raises(ValueError, match="Unknown timeline strategy: unknown_mode"):
        platform.get_timeline("unknown_mode", "alice", limit=10)


def test_reddit_unknown_timeline_mode_fails_loudly(tmp_path) -> None:
    platform = RedditLikePlatform(
        db_path=str(tmp_path / "reddit_default_timeline.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("general", "General discussion")
    platform.join_subreddit("alice", "general")
    platform.join_subreddit("bob", "general")
    platform.create_post("bob", "general", "fallback", "fallback-post")

    with pytest.raises(ValueError, match="Unknown timeline strategy: unknown_mode"):
        platform.get_timeline("unknown_mode", "alice", limit=10)
