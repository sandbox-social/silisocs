"""Regression tests for the negative/moderation social actions.

These actions (dislike / mute / report) were exposed to agents via ``@app_action``
but referenced SQLite tables that ``_init_db`` never created, so every call hit an
``OperationalError`` that was swallowed by a broad ``except`` and returned ``False``.
The feature was dead-on-arrival. These tests pin the schema + behaviour so the
regression cannot silently return.
"""

from __future__ import annotations

from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform

_NEGATIVE_ACTION_TABLES = {"dislikes", "mutes", "reports"}
# Reddit routes dislikes through the canonical `votes` table (downvote), so it has
# no separate `dislikes` table — only mute/report have dedicated tables.
_REDDIT_NEGATIVE_ACTION_TABLES = {"mutes", "reports"}


def _table_names(platform) -> set[str]:
    with platform.get_connection() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row[0] for row in rows}


# --------------------------------------------------------------------------- #
# Schema smoke tests
# --------------------------------------------------------------------------- #


def test_twitter_init_db_creates_negative_action_tables(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        assert _table_names(platform) >= _NEGATIVE_ACTION_TABLES
    finally:
        platform.shutdown()


def test_reddit_init_db_creates_negative_action_tables(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        tables = _table_names(platform)
        assert tables >= _REDDIT_NEGATIVE_ACTION_TABLES
        # Reddit dislikes are downvotes in the votes table; no parallel dislikes table.
        assert "dislikes" not in tables
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# Twitter behaviour
# --------------------------------------------------------------------------- #


def test_twitter_dislike_increments_count_is_idempotent_and_reversible(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        post_id = platform.create_post("bob", "hello world")

        def _count() -> int:
            with platform.get_connection() as conn:
                row = conn.execute(
                    "SELECT dislikes_count FROM posts WHERE id = ?", (post_id,)
                ).fetchone()
            return int(row["dislikes_count"])

        assert platform.dislike_post("alice", post_id) is True
        assert _count() == 1
        # Re-disliking is a no-op (does not double count).
        assert platform.dislike_post("alice", post_id) is False
        assert _count() == 1
        # Undo brings the count back to zero.
        assert platform.undo_dislike_post("alice", post_id) is True
        assert _count() == 0
    finally:
        platform.shutdown()


def test_twitter_mute_unmute_roundtrip(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")

        def _mute_rows() -> int:
            with platform.get_connection() as conn:
                return int(conn.execute("SELECT COUNT(*) AS c FROM mutes").fetchone()["c"])

        assert platform.mute_user("alice", "bob") is True
        assert _mute_rows() == 1
        assert platform.mute_user("alice", "bob") is False  # idempotent
        assert _mute_rows() == 1
        assert platform.unmute_user("alice", "bob") is True
        assert _mute_rows() == 0
    finally:
        platform.shutdown()


def test_twitter_report_post_inserts_row(tmp_path) -> None:
    platform = TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")
        post_id = platform.create_post("bob", "report me")

        assert platform.report_post("alice", post_id, reason="spam") is True
        with platform.get_connection() as conn:
            row = conn.execute(
                "SELECT reason FROM reports WHERE post_id = ?", (post_id,)
            ).fetchone()
        assert row is not None
        assert row["reason"] == "spam"
    finally:
        platform.shutdown()


# --------------------------------------------------------------------------- #
# Reddit behaviour
# --------------------------------------------------------------------------- #


def _reddit_world(platform) -> int:
    """Seed a subreddit with one post and return its id."""
    platform.create_user("alice")
    platform.create_user("bob")
    platform.create_subreddit("python", "About python")
    platform.join_subreddit("alice", "python")
    return platform.create_post("bob", "python", "Title", "content")


def test_reddit_dislike_increments_downvotes_and_is_reversible(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_world(platform)

        def _downvotes() -> int:
            with platform.get_connection() as conn:
                row = conn.execute(
                    "SELECT downvotes FROM posts WHERE id = ?", (post_id,)
                ).fetchone()
            return int(row["downvotes"])

        assert platform.dislike_post("alice", post_id) is True
        assert _downvotes() == 1
        assert platform.dislike_post("alice", post_id) is False  # idempotent
        assert _downvotes() == 1
        assert platform.undo_dislike_post("alice", post_id) is True
        assert _downvotes() == 0
    finally:
        platform.shutdown()


def test_reddit_mute_unmute_roundtrip(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        platform.create_user("alice")
        platform.create_user("bob")

        assert platform.mute_user("alice", "bob") is True
        assert platform.mute_user("alice", "bob") is False
        assert platform.unmute_user("alice", "bob") is True
    finally:
        platform.shutdown()


def test_reddit_report_post_inserts_row(tmp_path) -> None:
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_world(platform)

        assert platform.report_post("alice", post_id, reason="rule-break") is True
        with platform.get_connection() as conn:
            row = conn.execute(
                "SELECT reason FROM reports WHERE post_id = ?", (post_id,)
            ).fetchone()
        assert row is not None
        assert row["reason"] == "rule-break"
    finally:
        platform.shutdown()


def test_reddit_subreddit_lifecycle_and_feed(tmp_path) -> None:
    """Covers the subreddit actions that the app layer exposes once activated."""
    platform = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    try:
        post_id = _reddit_world(platform)
        assert post_id is not None

        feed = platform.get_subreddit_feed("python", limit=10)
        titles = [post.get("title") for post in feed.get("posts", [])]
        assert "Title" in titles

        # Leaving the subreddit succeeds without error.
        platform.leave_subreddit("alice", "python")
    finally:
        platform.shutdown()
