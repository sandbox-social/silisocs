"""Regression tests for the whole-repo review backend fixes.

Covers: twitter trending column indices, twitter unlike guard/clamp, twitter +
reddit mute feed filtering, reddit unlike_post (votes table), reddit
leave_subreddit guard, and reddit downvote/dislike single-counting.
"""

from __future__ import annotations

from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform


def _post_counts(platform, post_id: int) -> dict:
    with platform.get_connection() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
    return dict(row)


# --------------------------------------------------------------------------- #
# Twitter
# --------------------------------------------------------------------------- #


def test_twitter_trending_returns_correct_fields(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        post_id = platform.create_post("bob", "hello world")
        # Dislike bumps dislikes_count to 1 while reposts_count stays 0; this is
        # what previously leaked into the reposts_count field via a wrong index.
        platform.dislike_post("alice", post_id)

        trending = platform.get_trending_posts(limit=10, days=7)
        match = next(p for p in trending if p["id"] == post_id)
        assert match["content"] == "hello world"  # not created_at
        assert match["reposts_count"] == 0  # not dislikes_count (which is 1)
        assert isinstance(match["created_at"], (int, float))  # not the 'type' string
    finally:
        platform.shutdown()


def test_twitter_unlike_never_liked_does_not_go_negative(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        post_id = platform.create_post("bob", "hi")

        result = platform.unlike("alice", post_id)
        assert result is False
        assert _post_counts(platform, post_id)["likes_count"] == 0  # not -1
    finally:
        platform.shutdown()


def test_twitter_mute_filters_user_from_home_feed(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        platform.follow("alice", "bob")
        platform.create_post("bob", "from bob")

        before = platform.get_feed("chronological_home", "alice")
        assert any("from bob" in p.get("content", "") for p in before["posts"])

        platform.mute_user("alice", "bob")
        after = platform.get_feed("chronological_home", "alice")
        assert not any("from bob" in p.get("content", "") for p in after["posts"])
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# Reddit
# --------------------------------------------------------------------------- #


def _reddit_post(platform) -> int:
    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("python", "About python")
    platform.join_subreddit("alice", "python")
    return platform.create_post("bob", "python", "Title", "Body")


def test_reddit_unlike_post_removes_upvote(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_post(platform)
        platform.vote("alice", post_id, "post", 1)
        assert _post_counts(platform, post_id)["upvotes"] == 1

        assert platform.unlike_post("alice", post_id) is True
        assert _post_counts(platform, post_id)["upvotes"] == 0
        # A second unlike with no active upvote is a no-op.
        assert platform.unlike_post("alice", post_id) is False
        assert _post_counts(platform, post_id)["upvotes"] == 0
    finally:
        platform.shutdown()


def test_reddit_leave_subreddit_not_member_is_noop(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        platform.create_user("carol")
        platform.create_user("bob")
        platform.create_subreddit("python", "About python")

        with platform.get_connection() as conn:
            before = int(
                conn.execute(
                    "SELECT members_count FROM subreddits WHERE name = 'python'"
                ).fetchone()["members_count"]
            )

        # carol never joined; leaving must not decrement.
        assert platform.leave_subreddit("carol", "python") is False
        with platform.get_connection() as conn:
            after = int(
                conn.execute(
                    "SELECT members_count FROM subreddits WHERE name = 'python'"
                ).fetchone()["members_count"]
            )
        assert after == before
    finally:
        platform.shutdown()


def test_reddit_downvote_then_dislike_does_not_double_count(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_post(platform)
        platform.vote("alice", post_id, "post", -1)  # downvote action path
        assert _post_counts(platform, post_id)["downvotes"] == 1

        # dislike_post is the same underlying downvote -> already downvoted -> no-op.
        assert platform.dislike_post("alice", post_id) is False
        assert _post_counts(platform, post_id)["downvotes"] == 1
    finally:
        platform.shutdown()


def test_reddit_mute_filters_user_from_home_feed(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_post(platform)
        assert post_id is not None

        before = platform.get_feed("home", "alice")
        assert any(p.get("title") == "Title" for p in before["posts"])

        platform.mute_user("alice", "bob")
        after = platform.get_feed("home", "alice")
        assert not any(p.get("title") == "Title" for p in after["posts"])
    finally:
        platform.shutdown()
