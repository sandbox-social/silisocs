"""Tests for the shared ``SqliteSocialEngineBase``.

The twitter-like and reddit-like engines now share one base for the connection
pool, async write queue, and the platform-agnostic operations (users, DMs, block
list, mutes, reports). This also fixed a latent twitter perf bug: ``get_connection``
is now thread-local + WAL for both engines (twitter previously opened a fresh
connection per call).
"""

from __future__ import annotations

import threading

import pytest

from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform
from silisocs.environments.backends.social.sqlite_engine import SqliteSocialEngineBase
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform


def _twitter(tmp_path) -> TwitterLikePlatform:
    return TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)


def _reddit(tmp_path) -> RedditLikePlatform:
    p = RedditLikePlatform(db_path=str(tmp_path / "rd.db"), use_queue=False)
    p.create_subreddit("py", "desc")
    return p


@pytest.mark.parametrize(
    "default,cls",
    [("twitter_like.db", TwitterLikePlatform), ("reddit_like.db", RedditLikePlatform)],
)
def test_engines_subclass_shared_base_with_expected_default(default, cls) -> None:
    assert issubclass(cls, SqliteSocialEngineBase)
    assert cls.default_db_path == default


def test_get_connection_is_thread_local_and_wal_for_both_engines(tmp_path) -> None:
    for platform in (_twitter(tmp_path), _reddit(tmp_path)):
        try:
            with platform.get_connection() as conn:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode.lower() == "wal"
            # Same thread reuses the same connection object.
            with platform.get_connection() as c1:
                first = c1
            with platform.get_connection() as c2:
                assert c2 is first
        finally:
            platform.shutdown()


def test_inherited_operations_work_on_both_engines(tmp_path) -> None:
    tw = _twitter(tmp_path)
    rd = _reddit(tmp_path)
    try:
        for p in (tw, rd):
            assert p.create_user("alice") is not None
            assert p.create_user("bob") is not None
            assert p.get_user_id("alice") is not None
            profile = p.view_profile("alice")
            assert profile is not None and profile["username"] == "alice"
            # mute/unmute round-trip (inherited)
            assert p.mute_user("alice", "bob") is True
            assert p.unmute_user("alice", "bob") is True
            # DM send + blocked-DM guard (inherited)
            p.send_dm("alice", "bob", "hi")
            p.block("alice", "bob")  # domain method, but unblock is inherited
            assert p.unblock("alice", "bob") is not None
    finally:
        tw.shutdown()
        rd.shutdown()


def test_view_dms_with_marks_thread_read_on_both_engines(tmp_path) -> None:
    """The inherited view_dms_with marks the viewer's unread messages read."""
    tw = _twitter(tmp_path)
    rd = _reddit(tmp_path)
    try:
        for p in (tw, rd):
            p.create_user("alice")
            p.create_user("bob")
            p.send_dm("alice", "bob", "hi bob")
            thread = p.view_dms_with("bob", "alice")
            assert len(thread) == 1
            # A second view shows the message now marked read (previously a
            # reddit-vs-twitter drift: reddit never cleared unread state).
            assert all(msg["read"] for msg in p.view_dms_with("bob", "alice"))
    finally:
        tw.shutdown()
        rd.shutdown()


def test_connection_recovers_after_error_in_block(tmp_path) -> None:
    """An error inside a get_connection block must discard the thread-local conn."""
    p = _twitter(tmp_path)
    try:
        with pytest.raises(RuntimeError), p.get_connection():
            raise RuntimeError("boom")
        # Subsequent calls get a fresh, working connection.
        with p.get_connection() as conn:
            assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        p.shutdown()


def test_writer_thread_runs_when_queue_enabled(tmp_path) -> None:
    p = TwitterLikePlatform(db_path=str(tmp_path / "q.db"), use_queue=True)
    try:
        assert isinstance(p._writer_thread, threading.Thread)
        assert p._writer_thread.is_alive()
        uid = p.create_user("alice")  # async write, resolved via future
        assert uid is not None
    finally:
        p.shutdown()
        assert not p._writer_thread.is_alive()
