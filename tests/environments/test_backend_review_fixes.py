"""Regression tests for the whole-repo review backend fixes.

Covers: twitter trending column indices, twitter unlike guard/clamp, twitter +
reddit mute feed filtering, reddit unlike_post (votes table), reddit
leave_subreddit guard, reddit downvote/dislike single-counting, hallucinated
reply/comment target ids rejecting instead of raising IntegrityError, ``r/``
prefixed subreddit names in setup, and the recommendation commit staying inside
the connection block.
"""

from __future__ import annotations

import inspect

import pytest

from silisocs.environments.backends.reddit_like.app import RedditLikeApp, _subreddit_name
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


# --------------------------------------------------------------------------- #
# Hallucinated ids are agent mistakes, not backend bugs
# --------------------------------------------------------------------------- #


def test_twitter_reply_to_missing_post_raises_value_error(tmp_path) -> None:
    """A reply to a nonexistent id must reject, not raise sqlite3.IntegrityError.

    With ``PRAGMA foreign_keys=ON`` the unvalidated INSERT raised IntegrityError,
    which escapes the invoke layer's ``except ValueError`` guard: the agent's
    whole turn was discarded and ``backend_action_errors`` counted a backend bug
    for an agent hallucinating a post id.
    """
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")

        with pytest.raises(ValueError, match="not found"):
            platform.create_post("alice", "replying", reply_to_id=99999)

        # The rejection commits nothing: no orphan post row.
        with platform.get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"] == 0
    finally:
        platform.shutdown()


def test_twitter_reply_to_existing_post_still_works(tmp_path) -> None:
    """The validation guard must not break the normal reply path."""
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        parent = platform.create_post("bob", "parent")

        reply_id = platform.create_post("alice", "child", reply_to_id=parent)

        assert reply_id
        assert _post_counts(platform, parent)["reply_count"] == 1
        with platform.get_connection() as conn:
            activities = conn.execute(
                "SELECT action_type FROM activities WHERE post_id = ?", (parent,)
            ).fetchall()
        assert [dict(row)["action_type"] for row in activities] == ["reply"]
    finally:
        platform.shutdown()


def test_reddit_comment_on_missing_post_or_parent_raises_value_error(tmp_path) -> None:
    """The reddit twin of the twitter reply guard (already correct; pinned here)."""
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_post(platform)

        with pytest.raises(ValueError, match="Post not found"):
            platform.create_comment("alice", 99999, "hi")
        with pytest.raises(ValueError, match="Parent comment not found"):
            platform.create_comment("alice", post_id, "hi", parent_id=88888)

        with platform.get_connection() as conn:
            assert conn.execute("SELECT COUNT(*) AS n FROM comments").fetchone()["n"] == 0
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# Subreddit name normalization
# --------------------------------------------------------------------------- #


def test_reddit_setup_accepts_r_slash_prefixed_subreddit_names(tmp_path) -> None:
    """``name: "r/politics"`` must create and subscribe to the SAME community.

    ``create_subreddit`` strips the prefix internally but
    ``join_subreddit``/``get_subreddit_id`` do not, so setup created ``politics``
    and then aborted the run looking for ``r/politics``.
    """
    app = RedditLikeApp(db_path=str(tmp_path / "rd.db"))
    try:
        app.setup_social_state(
            agent_names=["Alice", "Bob"],
            graph_config={"subreddits": [{"name": "r/politics", "roles": "all"}]},
        )

        stats = app._last_initialization_stats
        assert stats["num_subreddits"] == 1
        assert stats["num_subscriptions"] == 2
        assert app._platform.get_subreddit_id("politics") is not None
        assert app._platform.get_subreddit_id("r/politics") is None
    finally:
        app.shutdown()


def test_subreddit_name_helper_normalizes_and_still_rejects_empty() -> None:
    assert _subreddit_name({"name": "r/politics"}) == "politics"
    assert _subreddit_name("r/politics") == "politics"
    assert _subreddit_name({"name": "politics"}) == "politics"
    for bad in ({"names": "politics"}, {"name": "  "}, {"name": "r/"}):
        with pytest.raises(ValueError, match="non-empty 'name'"):
            _subreddit_name(bad)


# --------------------------------------------------------------------------- #
# update_recommendations commits inside the connection context
# --------------------------------------------------------------------------- #


def test_twitter_update_recommendations_commits_inside_connection_block() -> None:
    """A commit failure must reach get_connection's discard-and-reraise cleanup.

    Committing after the ``with`` left an open write transaction on a
    thread-local connection the pool keeps handing back.
    """
    source = inspect.getsource(TwitterLikePlatform.update_recommendations)
    body = source[source.index("with self.get_connection() as conn:") :]
    commit_line = next(line for line in body.splitlines() if "conn.commit()" in line)
    indent = len(commit_line) - len(commit_line.lstrip())
    with_line = next(
        line for line in body.splitlines() if "with self.get_connection() as conn:" in line
    )
    assert indent > len(with_line) - len(with_line.lstrip())
