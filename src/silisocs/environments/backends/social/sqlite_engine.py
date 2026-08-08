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
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from typing import Any, ClassVar

logger = logging.getLogger("SqliteSocialEngine")


def _recsys_source(recsys_type: str | None) -> str:
    """Exposure-source tag for a recommender-sourced post."""
    return f"recsys:{recsys_type}" if recsys_type else "recsys"


def _tag_source(posts: list[dict], source: str) -> list[dict]:
    """Annotate each post dict with its timeline ``source`` (for exposure logging).

    Applied at the ``get_timeline`` dispatch (not in the underlying feed/recsys
    queries, whose other callers must stay untouched), on the freshly-built dicts
    those queries return. Formatters read a fixed field set, so the extra key
    leaves observation text unchanged.
    """
    for post in posts:
        if isinstance(post, dict):
            post["source"] = source
    return posts


class SqliteSocialEngineBase:
    """SQLite-backed social engine: shared connection/queue + common operations."""

    default_db_path: ClassVar[str] = "social.db"

    def __init__(self, db_path: str | None = None, use_queue: bool = True):
        self.db_path = db_path or self.default_db_path
        self._local = threading.local()
        # username -> id memo. Users are never renamed or deleted during a run,
        # so entries stay valid until the whole DB is replaced. Shipped apps
        # rebuild the entire platform object on checkpoint restore (fresh empty
        # cache); a custom backend that swaps the DB file in place instead must
        # call invalidate_user_id_cache(). Saves 1-2 SELECT round-trips on
        # nearly every action.
        self._user_id_cache: dict[str, int] = {}
        # Registry of every connection handed out by get_connection(), keyed to
        # its owning thread, so they can all be closed on shutdown() AND so
        # connections stranded by dead threads can be reaped as new ones open.
        # Without reaping, per-step thread pools (a fresh pool per step under
        # the threads executor) leak one open connection per worker per step —
        # thousands of fds over a long run.
        self._connections: dict[sqlite3.Connection, threading.Thread] = {}
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
    def _iter_in_chunks(ids: Sequence[Any], size: int = 500) -> Iterator[Sequence[Any]]:
        """Yield ``ids`` in chunks safe for one ``IN (?,...)`` clause.

        SQLite's bound-variable cap (``SQLITE_MAX_VARIABLE_NUMBER``) is 999 on
        older builds; a population-scaled id list (e.g. active users at 1000+
        agents) blown into a single IN clause raises "too many SQL variables".
        Chunking keeps every statement well under the cap.
        """
        for start in range(0, len(ids), size):
            yield ids[start : start + size]

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
                self._reap_dead_connections_locked()
                self._connections[conn] = threading.current_thread()
        try:
            yield conn
        except Exception:
            # On error, discard the connection so next call gets a fresh one.
            try:
                conn.close()
            except Exception:
                pass
            with self._connections_lock:
                self._connections.pop(conn, None)
            self._local.conn = None
            raise

    def _reap_dead_connections_locked(self) -> None:
        """Close connections whose owning thread has exited (lock held).

        A thread-local connection is only ever used by its owning thread, so
        once that thread is dead the handle is unreachable and safe to close.
        Called on the new-connection path — exactly when pool churn happens —
        so steady-state per-call overhead is zero.
        """
        dead = [c for c, t in self._connections.items() if not t.is_alive()]
        for conn in dead:
            del self._connections[conn]
            try:
                conn.close()
            except Exception:
                pass

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
        cached = self._user_id_cache.get(username)
        if cached is not None:
            return cached
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                self._user_id_cache[username] = int(row["id"])
                return int(row["id"])
            return None

    def invalidate_user_id_cache(self) -> None:
        """Drop the username->id memo. Call whenever the DB is replaced wholesale
        (checkpoint restore), where ids may no longer match the cached mapping.
        """
        self._user_id_cache.clear()

    def create_users(self, users: Sequence[tuple[str, str]], sync: bool = True) -> Any:
        """Bulk-register users in ONE queued transaction (init fast path).

        ``users`` is ``(username, bio)`` pairs; existing usernames are skipped
        (same idempotency as :meth:`create_user`). At setup scale this replaces
        N serialized create round-trips with a single writer-queue item.
        """
        now = time.time()
        queries = [
            (
                "INSERT OR IGNORE INTO users (username, bio, created_at) VALUES (?, ?, ?)",
                (username, bio, now),
            )
            for username, bio in users
        ]
        if not queries:
            return None
        return self._execute_write(queries, sync=sync)

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
            result = self._execute_write(queries, sync=sync)
            if sync and isinstance(result, int):
                self._user_id_cache[username] = result
            return result
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
            missing = username if not sender_id else target_username
            raise ValueError(f"send_dm: user '{missing}' not found")

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
            missing = username if not user_id else target_username
            raise ValueError(f"view_dms_with: user '{missing}' not found")

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
            missing = username if not blocker_id else target_username
            raise ValueError(f"unblock: user '{missing}' not found")

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

    def _recommendation_posts(self, rows: list[sqlite3.Row]) -> list[dict]:
        """Project recommendation query rows into post dicts.

        The default is a plain row->dict mapping; a backend that enriches posts
        (e.g. Twitter's ``_parse_posts``) overrides this.
        """
        return [dict(r) for r in rows]

    def get_recommendations(
        self, username: str, limit: int = 10, recsys_type: str | None = None
    ) -> list[dict]:
        """Get recommended posts for a user (shared across social backends)."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return []
                if recsys_type:
                    cursor = conn.execute(
                        """
                        SELECT p.*, u.username
                        FROM recommendations r
                        JOIN posts p ON r.post_id = p.id
                        JOIN users u ON p.user_id = u.id
                        WHERE r.user_id = ? AND r.recsys_type = ?
                        LIMIT ?
                        """,
                        (user_id, recsys_type, limit),
                    )
                else:
                    cursor = conn.execute(
                        """
                        SELECT p.*, u.username
                        FROM recommendations r
                        JOIN posts p ON r.post_id = p.id
                        JOIN users u ON p.user_id = u.id
                        WHERE r.user_id = ?
                        LIMIT ?
                        """,
                        (user_id, limit),
                    )
                return self._recommendation_posts(cursor.fetchall())
        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return []

    # get_feed is provided by each subclass (signatures vary per backend); the shared
    # timeline dispatch below calls it as get_feed(strategy, username, limit=...).
    get_feed: Callable[..., dict[str, Any]]

    # Feed strategy name for the "follower" source in get_timeline's blends. Twitter
    # overrides this to "chronological_home"; Reddit inherits the default "home". A
    # class attribute so subclasses pick their own home-feed name without
    # reimplementing the (identical) strategy dispatch and blend below.
    _follower_feed_strategy: str = "home"

    def get_timeline(
        self,
        strategy: str,
        username: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: Any,
    ) -> list[dict]:
        """Resolve a timeline strategy shared across the SQLite social backends.

        Handles the strategies common to every backend (``follower_chronological``,
        ``pure_recsys``, ``hybrid_recsys_follower``). A subclass with extra strategies
        (e.g. Twitter's ``curated_global``) handles those first and delegates the rest
        here via ``super().get_timeline``.
        """
        if strategy == "follower_chronological":
            feed = self.get_feed(self._follower_feed_strategy, username, limit=limit)
            return _tag_source((feed or {}).get("posts", []), "follower")
        if strategy == "pure_recsys":
            posts = self.get_recommendations(username, limit, recsys_type=recsys_type)
            return _tag_source(posts, _recsys_source(recsys_type))
        if strategy == "hybrid_recsys_follower":
            return self._blend_recsys_and_follower(
                username, limit, recsys_type=recsys_type, **timeline_config
            )
        raise ValueError(f"Unknown timeline strategy: {strategy}")

    def _blend_recsys_and_follower(
        self,
        username: str,
        limit: int,
        recsys_type: str | None = None,
        **timeline_config: Any,
    ) -> list[dict]:
        """Blend recommendations (higher priority) with the follower feed, deduped by id."""
        recsys_ratio = timeline_config.get("recsys_ratio", 0.6)
        follower_ratio = timeline_config.get("follower_ratio", 0.4)
        rec_count = max(1, int(limit * recsys_ratio))
        follower_count = max(1, int(limit * follower_ratio))
        rec_posts = _tag_source(
            self.get_recommendations(username, rec_count, recsys_type=recsys_type),
            _recsys_source(recsys_type),
        )
        follow_feed = self.get_feed(self._follower_feed_strategy, username, limit=follower_count)
        follow_posts = _tag_source((follow_feed or {}).get("posts", []), "follower")
        # Recsys first (higher priority), then the follower feed; dedup by post id.
        seen_ids: set[Any] = set()
        combined: list[dict] = []
        for post in list(rec_posts or []) + list(follow_posts):
            post_id = post.get("id", post.get("post_id"))
            if post_id not in seen_ids:
                combined.append(post)
                seen_ids.add(post_id)
        return combined[:limit]
