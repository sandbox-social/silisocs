"""Regression tests for ``SqliteSocialEngineBase`` code-review fixes.

Covers:
1. ``shutdown()`` closes every connection that ``get_connection()`` opened
   (no thread-local read/direct connection leaks), and clears the thread-local.
2. ``PRAGMA foreign_keys=ON`` is enforced on the direct ``get_connection()``
   path, so an FK-violating insert raises instead of silently succeeding.
3. ``create_user`` is idempotent under the async write queue (``sync=False``):
   a repeated username returns the SAME id without raising / leaking the
   ``IntegrityError`` onto the returned future.

Plus a couple of guards for the lower-priority fixes (non-INSERT write result =
rowcount; the writer connection is closed exactly once on shutdown).
"""

from __future__ import annotations

import concurrent.futures
import sqlite3

import pytest

from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform


def _twitter_sync(tmp_path) -> TwitterLikePlatform:
    return TwitterLikePlatform(db_path=str(tmp_path / "tw.db"), use_queue=False)


def _twitter_queue(tmp_path) -> TwitterLikePlatform:
    return TwitterLikePlatform(db_path=str(tmp_path / "twq.db"), use_queue=True)


# ---------------------------------------------------------------------------
# (1) Connection-leak fix: shutdown() closes opened connections
# ---------------------------------------------------------------------------


def test_shutdown_closes_get_connection_connections(tmp_path) -> None:
    p = _twitter_sync(tmp_path)
    # Open a thread-local read/direct connection on this thread.
    with p.get_connection() as conn:
        assert conn.execute("SELECT 1").fetchone()[0] == 1

    # The connection is tracked in the registry and is currently usable.
    assert len(p._connections) == 1
    (tracked,) = tuple(p._connections)
    assert tracked.execute("SELECT 1").fetchone()[0] == 1  # still open

    p.shutdown()

    # Registry is cleared and the tracked connection is actually closed.
    assert len(p._connections) == 0
    with pytest.raises(sqlite3.ProgrammingError):
        tracked.execute("SELECT 1")
    # Thread-local cache was dropped.
    assert getattr(p._local, "conn", None) is None


def test_shutdown_closes_connections_opened_in_another_thread(tmp_path) -> None:
    """Connections are opened with check_same_thread=False so the shutdown
    thread can close a connection that a *different* thread opened.
    """
    p = _twitter_sync(tmp_path)
    try:
        opened: list[sqlite3.Connection] = []

        def _open() -> None:
            with p.get_connection() as conn:
                conn.execute("SELECT 1").fetchone()
            opened.append(next(iter(p._connections)))

        import threading

        t = threading.Thread(target=_open)
        t.start()
        t.join()

        assert len(p._connections) == 1
        other_thread_conn = opened[0]
    finally:
        p.shutdown()

    # Closed from the main (shutdown) thread despite being opened elsewhere.
    assert len(p._connections) == 0
    with pytest.raises(sqlite3.ProgrammingError):
        other_thread_conn.execute("SELECT 1")


def test_writer_connection_closed_exactly_once_and_no_double_shutdown_error(
    tmp_path,
) -> None:
    """Queue-mode shutdown joins the writer (which closes its own conn via the
    `with` block) and closes the registry connections; calling it twice is a
    no-op rather than an error.
    """
    p = _twitter_queue(tmp_path)
    with p.get_connection() as conn:
        conn.execute("SELECT 1").fetchone()
    p.shutdown()
    assert len(p._connections) == 0
    # Second shutdown must not raise (registry already empty, thread joined).
    p.shutdown()


# ---------------------------------------------------------------------------
# (2) Foreign-keys enforcement on the direct get_connection() path
# ---------------------------------------------------------------------------


def test_get_connection_enforces_foreign_keys(tmp_path) -> None:
    p = _twitter_sync(tmp_path)
    try:
        with p.get_connection() as conn:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            # posts.user_id has a FK to users(id); 999999 has no such user.
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO posts (user_id, content, created_at) VALUES (?, ?, ?)",
                    (999999, "orphan", 0.0),
                )
                conn.commit()
    finally:
        p.shutdown()


# ---------------------------------------------------------------------------
# (3) create_user idempotency under the async queue (sync=False)
# ---------------------------------------------------------------------------


def test_create_user_idempotent_under_queue_sync_false(tmp_path) -> None:
    p = _twitter_queue(tmp_path)
    try:
        first = p.create_user("alice", sync=False)
        # First call may return a future (fresh insert) or an int; resolve it.
        first_id = first.result() if isinstance(first, concurrent.futures.Future) else first
        assert isinstance(first_id, int)

        # Second call for the SAME username must not raise and must return the
        # same id (the existence check short-circuits before enqueuing a
        # duplicate INSERT whose IntegrityError would escape via the future).
        second = p.create_user("alice", sync=False)
        second_id = second.result() if isinstance(second, concurrent.futures.Future) else second
        assert second_id == first_id
    finally:
        p.shutdown()


def test_create_user_idempotent_sync_true(tmp_path) -> None:
    p = _twitter_queue(tmp_path)
    try:
        a = p.create_user("bob", sync=True)
        b = p.create_user("bob", sync=True)
        assert a == b
        assert p.get_user_id("bob") == a
    finally:
        p.shutdown()


# ---------------------------------------------------------------------------
# (4) Non-INSERT writes report affected-row counts, not a stale lastrowid
# ---------------------------------------------------------------------------


def test_non_insert_write_returns_rowcount_sync(tmp_path) -> None:
    p = _twitter_sync(tmp_path)
    try:
        uid = p.create_user("carol")
        assert isinstance(uid, int)
        # A bio UPDATE affects exactly one row; result should be the rowcount (1),
        # not the sticky lastrowid from the prior INSERT.
        res = p._execute_write(
            [("UPDATE users SET bio = ? WHERE id = ?", ("hello", uid))],
            sync=True,
        )
        assert res == 1
        # An UPDATE matching nothing affects zero rows.
        res0 = p._execute_write(
            [("UPDATE users SET bio = ? WHERE id = ?", ("x", 987654))],
            sync=True,
        )
        assert res0 == 0
    finally:
        p.shutdown()
