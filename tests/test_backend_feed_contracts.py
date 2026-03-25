from __future__ import annotations

from mastodon_sim.environments.backends.reddit_like.engine import RedditLikePlatform
from mastodon_sim.environments.backends.twitter_like.engine import TwitterLikePlatform


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


def test_twitter_unknown_timeline_mode_falls_back_to_follower_chronological(tmp_path) -> None:
    platform = TwitterLikePlatform(
        db_path=str(tmp_path / "twitter_default_timeline.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.follow("alice", "bob")
    platform.create_post("bob", "fallback-post")

    unknown_timeline = platform.get_timeline("unknown_mode", "alice", limit=10)
    follower_timeline = platform.get_timeline("follower_chronological", "alice", limit=10)

    assert [post["id"] for post in unknown_timeline] == [post["id"] for post in follower_timeline]


def test_reddit_unknown_timeline_mode_falls_back_to_follower_chronological(tmp_path) -> None:
    platform = RedditLikePlatform(
        db_path=str(tmp_path / "reddit_default_timeline.db"), use_queue=False
    )

    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("general", "General discussion")
    platform.join_subreddit("alice", "general")
    platform.join_subreddit("bob", "general")
    platform.create_post("bob", "general", "fallback", "fallback-post")

    unknown_timeline = platform.get_timeline("unknown_mode", "alice", limit=10)
    follower_timeline = platform.get_timeline("follower_chronological", "alice", limit=10)

    assert [post["id"] for post in unknown_timeline] == [post["id"] for post in follower_timeline]
