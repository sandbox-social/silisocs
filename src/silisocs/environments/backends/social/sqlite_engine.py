"""Shared SQLite engine infrastructure for social-media backends.

``SqliteSocialEngineBase`` factors out the platform-agnostic machinery shared by
the twitter-like and reddit-like engines: a thread-local connection pool (WAL +
busy-timeout), the async write queue + background writer thread, and the common
operations (users, direct messages, block list, mutes, reports) plus the
recommendation schema.

Subclasses implement the domain schema (``_init_db``), post parsing, feeds, and
the recommendation algorithms, and set ``default_db_path``.
"""

from __future__ import annotations

import concurrent.futures
import logging
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, ClassVar

logger = logging.getLogger("SqliteSocialEngine")


class SqliteSocialEngineBase:
    """SQLite-backed social engine: shared connection/queue + common operations."""

    default_db_path: ClassVar[str] = "social.db"

    def __init__(self, db_path: str | None = None, use_queue: bool = True):
        self.db_path = db_path or self.default_db_path
        self._local = threading.local()
        # Registry of every connection handed out by get_connection() so they can
        # all be closed on shutdown(), regardless of which thread opened them.
        self._connections: set[sqlite3.Connection] = set()
        self._connections_lock = threading.Lock()
        # Schema is created synchronously (DDL only) before the writer thread
        # starts: a concurrent writer connection would contend with _init_db's
        # `PRAGMA journal_mode=WAL` and risk a "database is locked" error.
        self._init_db()
        self.use_queue = use_queue

        if self.use_queue:
            self._write_queue: queue.Queue[Any] = queue.Queue()
            self._stop_event = threading.Event()
            self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self._writer_thread.start()

    def _init_db(self) -> None:
        """Create the platform-specific schema. Implemented by each subclass."""
        raise NotImplementedError

    @staticmethod
    def _write_result(cursor: sqlite3.Cursor, sql: str) -> Any:
        """Choose the result for a main write statement.

        For INSERT/REPLACE return the new ``lastrowid``; for other statements
        (UPDATE/DELETE) return ``rowcount`` instead. ``lastrowid`` is sticky on a
        long-lived connection, so it would otherwise echo a stale prior insert id
        for non-insert statements.
        """
        keyword = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
        if keyword in ("INSERT", "REPLACE"):
            return cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount
        return cursor.rowcount

    @contextmanager
    def get_connection(self):
        """Yields a thread-local database connection (reused across calls)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            # check_same_thread=False so shutdown() can close the connection from
            # whichever thread runs it, even though it is reused thread-locally.
            conn = sqlite3.connect(self.db_path, timeout=5.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
            with self._connections_lock:
                self._connections.add(conn)
        try:
            yield conn
        except Exception:
            # On error, discard the connection so next call gets a fresh one.
            try:
                conn.close()
            except Exception:
                pass
            with self._connections_lock:
                self._connections.discard(conn)
            self._local.conn = None
            raise

    def _writer_loop(self):
        """Background thread that consumes from the queue and batches writes."""
        # NOTE: ``with sqlite3.connect(...) as conn`` commits/rolls back on exit but
        # does NOT close the connection, so the writer connection is owned explicitly
        # and closed in the finally below when the loop stops.
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")

            while not self._stop_event.is_set() or not self._write_queue.empty():
                try:
                    item = self._write_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if item is None:
                    continue

                batch = [item]
                while len(batch) < 1000:
                    try:
                        next_item = self._write_queue.get_nowait()
                        if next_item is None:
                            break
                        batch.append(next_item)
                    except queue.Empty:
                        break

                # One SAVEPOINT per item so a per-item failure rolls back only that
                # item's partial writes (e.g. an INSERT that succeeded before a later
                # FK violation), not the whole batch.
                results = []
                try:
                    conn.execute("BEGIN TRANSACTION")
                    for idx, item_data in enumerate(batch):
                        queries, future = item_data
                        savepoint = f"item_{idx}"
                        try:
                            conn.execute(f"SAVEPOINT {savepoint}")
                            # Main query
                            main_sql, main_params = queries[0]
                            cursor = conn.execute(main_sql, main_params)
                            res = self._write_result(cursor, main_sql)
                            # Subsidiary queries (updates)
                            for sql, params in queries[1:]:
                                conn.execute(sql, params)
                            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                            results.append((future, res, None))
                        except Exception as e:
                            # Undo this item's partial writes, keep the rest of the batch.
                            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                            results.append((future, None, e))

                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.error(f"Batch write transaction failed entirely: {e}")
                    for _, future in batch:
                        if future and not future.done():
                            future.set_exception(e)
                    continue

                # Resolve futures after successful commit
                for future, res, err in results:
                    if future and not future.done():
                        if err:
                            future.set_exception(err)
                        else:
                            future.set_result(res)
        finally:
            conn.close()

    def _execute_write(self, queries: list[tuple[str, tuple[Any, ...]]], sync: bool = True) -> Any:
        """Helper to either enqueue a write or execute it directly."""
        if not self.use_queue:
            with self.get_connection() as conn:
                try:
                    main_sql, main_params = queries[0]
                    cursor = conn.execute(main_sql, main_params)
                    res = self._write_result(cursor, main_sql)
                    for sql, params in queries[1:]:
                        conn.execute(sql, params)
                    conn.commit()
                    return res
                except Exception as e:
                    conn.rollback()
                    raise e
        else:
            future: concurrent.futures.Future[Any] = concurrent.futures.Future()
            self._write_queue.put((queries, future))
            if sync:
                return future.result()
            return future

    def shutdown(self):
        """Clean shutdown of the writer thread and all read/direct connections."""
        if self.use_queue:
            self._stop_event.set()
            # Push a sentinel value to wake up the queue
            self._write_queue.put(None)
            self._writer_thread.join()

        # Close every connection get_connection() opened (across all threads). The
        # writer thread owns and closes its own connection in _writer_loop's finally
        # (joined above), so it is intentionally not part of this registry.
        with self._connections_lock:
            connections = list(self._connections)
            self._connections.clear()
        for conn in connections:
            try:
                conn.close()
            except Exception:
                pass
        # Drop the cached connection on the calling thread so a post-shutdown
        # get_connection() opens a fresh one rather than reusing a closed handle.
        self._local.conn = None

    def _init_recommendation_schema(self, conn: sqlite3.Connection) -> None:
        """Initialize optional recommendation extension schema.

        Recommendation-driven timelines rely on this table at runtime.
        """
        conn.execute("""
            CREATE TABLE IF NOT EXISTS recommendations (
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                recsys_type TEXT NOT NULL,
                PRIMARY KEY (user_id, post_id, recsys_type),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(post_id) REFERENCES posts(id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_recommendations_user_type ON recommendations(user_id, recsys_type)"
        )

    # ------------------------------------------------------------------ #
    # Shared user / DM / moderation operations
    # ------------------------------------------------------------------ #

    def get_user_id(self, username: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            return row["id"] if row else None

    def create_user(self, username: str, bio: str = "", sync: bool = True) -> Any:
        """Register a new user, returning the (existing or new) user id.

        Idempotent for serialized callers (the normal single-threaded init path):
        an up-front existence check returns the existing id, which matters under
        the async queue with ``sync=False`` where the ``UNIQUE`` ``IntegrityError``
        would otherwise be set on the returned future and escape. Note: two truly
        concurrent ``sync=False`` creations of the same brand-new username can still
        surface ``IntegrityError`` on the future (residual TOCTOU); shipped callers
        create users serially, so this does not arise in practice.
        """
        existing_id = self.get_user_id(username)
        if existing_id is not None:
            return existing_id
        try:
            queries = [
                (
                    "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
                    (username, bio, time.time()),
                )
            ]
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            # Lost an insert race (synchronous path): the row now exists.
            return self.get_user_id(username)

    def view_profile(self, username: str) -> dict[str, Any] | None:
        """View a user's full profile including basic stats."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return dict(row)
            return None

    def send_dm(self, username: str, target_username: str, content: str, sync: bool = True) -> Any:
        # Prevent blocked DMs
        sender_id = self.get_user_id(username)
        receiver_id = self.get_user_id(target_username)
        if not sender_id or not receiver_id:
            raise ValueError("User not found")

        with self.get_connection() as conn:
            if conn.execute(
                "SELECT 1 FROM blocks WHERE blocker_id = ? AND blocked_id = ?",
                (receiver_id, sender_id),
            ).fetchone():
                raise ValueError("Cannot send DM, blocked.")

        queries = [
            (
                "INSERT INTO direct_messages (sender_id, receiver_id, content, created_at) VALUES (?, ?, ?, ?)",
                (sender_id, receiver_id, content, time.time()),
            )
        ]
        return self._execute_write(queries, sync=sync)

    def view_dms_with(
        self, username: str, target_username: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Return the DM thread between two users, marking the viewer's unread ones read."""
        user_id = self.get_user_id(username)
        target_id = self.get_user_id(target_username)
        if not user_id or not target_id:
            raise ValueError("User not found")

        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT dm.*, u1.username as sender_username, u2.username as receiver_username
                FROM direct_messages dm
                JOIN users u1 ON dm.sender_id = u1.id
                JOIN users u2 ON dm.receiver_id = u2.id
                WHERE (dm.sender_id = ? AND dm.receiver_id = ?)
                   OR (dm.sender_id = ? AND dm.receiver_id = ?)
                ORDER BY dm.created_at ASC
                LIMIT ?
                """,
                (user_id, target_id, target_id, user_id, limit),
            ).fetchall()

            unread_ids = [r["id"] for r in rows if r["receiver_id"] == user_id and not r["read"]]
            if unread_ids:
                placeholders = ",".join(["?"] * len(unread_ids))
                conn.execute(
                    f"UPDATE direct_messages SET read = 1 WHERE id IN ({placeholders})", unread_ids
                )
                conn.commit()

            return [dict(r) for r in rows]

    def unblock(self, username: str, target_username: str, sync: bool = True):
        blocker_id = self.get_user_id(username)
        blocked_id = self.get_user_id(target_username)
        if not blocker_id or not blocked_id:
            raise ValueError("User not found")

        queries = [
            ("DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?", (blocker_id, blocked_id))
        ]
        return self._execute_write(queries, sync=sync)

    def mute_user(self, username: str, target_username: str) -> bool:
        """Mute another user."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                target_id = self.get_user_id(target_username)
                if not user_id or not target_id:
                    return False

                # Check if already muted
                cursor = conn.execute(
                    "SELECT 1 FROM mutes WHERE muter_id = ? AND mutee_id = ?",
                    (user_id, target_id),
                )
                if cursor.fetchone():
                    return False

                # Add mute
                now = time.time()
                conn.execute(
                    "INSERT INTO mutes (muter_id, mutee_id, created_at) VALUES (?, ?, ?)",
                    (user_id, target_id, now),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error muting user {target_username}: {e}")
            return False

    def unmute_user(self, username: str, target_username: str) -> bool:
        """Unmute a user."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                target_id = self.get_user_id(target_username)
                if not user_id or not target_id:
                    return False

                # Check if muted
                cursor = conn.execute(
                    "SELECT 1 FROM mutes WHERE muter_id = ? AND mutee_id = ?",
                    (user_id, target_id),
                )
                if not cursor.fetchone():
                    return False

                # Remove mute
                conn.execute(
                    "DELETE FROM mutes WHERE muter_id = ? AND mutee_id = ?",
                    (user_id, target_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error unmuting user {target_username}: {e}")
            return False

    def report_post(self, username: str, post_id: int, reason: str = "") -> bool:
        """Report a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                now = time.time()
                conn.execute(
                    "INSERT INTO reports (user_id, post_id, reason, created_at) VALUES (?, ?, ?, ?)",
                    (user_id, post_id, reason, now),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error reporting post {post_id}: {e}")
            return False
