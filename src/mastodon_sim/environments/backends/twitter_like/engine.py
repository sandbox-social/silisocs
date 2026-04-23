from __future__ import annotations

import concurrent.futures
import logging
import os
import queue
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("TwitterLikePlatform")


@dataclass
class Post:
    id: int
    user_id: int
    username: str
    content: str
    created_at: float
    likes_count: int
    reposts_count: int
    reply_count: int
    type: str  # 'post', 'repost', 'quote'
    reply_to_id: int | None = None
    quote_of_id: int | None = None
    formatted_date: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "content": self.content,
            "created_at": self.created_at,
            "likes_count": self.likes_count,
            "reposts_count": self.reposts_count,
            "reply_count": self.reply_count,
            "type": self.type,
            "reply_to_id": self.reply_to_id,
            "quote_of_id": self.quote_of_id,
            "formatted_date": self.formatted_date,
        }


class TwitterLikePlatform:
    SUPPORTED_RECSYS_TYPES = frozenset({"twitter", "twitter_tfidf", "twhin"})

    def __init__(self, db_path: str = "twitter_like.db", use_queue: bool = True):
        self.db_path = db_path
        self._init_db()
        self.use_queue = use_queue

        if self.use_queue:
            self._write_queue: queue.Queue[Any] = queue.Queue()
            self._stop_event = threading.Event()
            self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
            self._writer_thread.start()

    def _init_db(self):
        """Initialize the database schema with optimizations and advanced features."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
            # Enable WAL mode for high concurrency
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            conn.execute("PRAGMA foreign_keys=ON;")

            # Users table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    bio TEXT,
                    created_at REAL,
                    followers_count INTEGER DEFAULT 0,
                    following_count INTEGER DEFAULT 0,
                    posts_count INTEGER DEFAULT 0
                )
            """)

            # Posts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    content TEXT,
                    created_at REAL,
                    type TEXT DEFAULT 'post',  -- 'post', 'repost', 'quote'
                    reply_to_id INTEGER,
                    quote_of_id INTEGER,
                    likes_count INTEGER DEFAULT 0,
                    dislikes_count INTEGER DEFAULT 0,
                    reposts_count INTEGER DEFAULT 0,
                    reply_count INTEGER DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(reply_to_id) REFERENCES posts(id),
                    FOREIGN KEY(quote_of_id) REFERENCES posts(id)
                )
            """)

            # Indexes for high performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_user_created ON posts(user_id, created_at DESC)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC)")
            # FTS-friendly index could be added, but simple glob/like is often fine on local DBs

            # Follows table (Many-to-Many)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS follows (
                    follower_id INTEGER NOT NULL,
                    followee_id INTEGER NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (follower_id, followee_id),
                    FOREIGN KEY(follower_id) REFERENCES users(id),
                    FOREIGN KEY(followee_id) REFERENCES users(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_follows_followee ON follows(followee_id)")

            # Likes table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS likes (
                    user_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (user_id, post_id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(post_id) REFERENCES posts(id)
                )
            """)

            # Blocks table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS blocks (
                    blocker_id INTEGER NOT NULL,
                    blocked_id INTEGER NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (blocker_id, blocked_id),
                    FOREIGN KEY(blocker_id) REFERENCES users(id),
                    FOREIGN KEY(blocked_id) REFERENCES users(id)
                )
            """)

            # Direct Messages table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS direct_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender_id INTEGER NOT NULL,
                    receiver_id INTEGER NOT NULL,
                    content TEXT,
                    created_at REAL,
                    read BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY(sender_id) REFERENCES users(id),
                    FOREIGN KEY(receiver_id) REFERENCES users(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dms ON direct_messages(receiver_id, sender_id)"
            )

            # Activities/Notifications table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_user_id INTEGER NOT NULL,
                    source_user_id INTEGER NOT NULL,
                    action_type TEXT NOT NULL, -- 'like', 'repost', 'follow', 'mention', 'reply'
                    post_id INTEGER,
                    created_at REAL,
                    read BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY(target_user_id) REFERENCES users(id),
                    FOREIGN KEY(source_user_id) REFERENCES users(id),
                    FOREIGN KEY(post_id) REFERENCES posts(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_activities_target ON activities(target_user_id, created_at DESC)"
            )

            # Initialize OASIS schema tables
            self._init_oasis_schema(conn)

    def _init_oasis_schema(self, conn: sqlite3.Connection) -> None:
        """Initialize optional OASIS extension schema.

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

    def shutdown(self):
        """Clean shutdown of the writer thread."""
        if self.use_queue:
            self._stop_event.set()
            # Push a sentinel value to wake up the queue
            self._write_queue.put(None)
            self._writer_thread.join()

    @contextmanager
    def get_connection(self):
        """Yields a database connection context."""
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _writer_loop(self):
        """Background thread that consumes from the queue and batches writes."""
        with sqlite3.connect(self.db_path, timeout=5.0) as conn:
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

                # Execute the batch inside a single transaction
                results = []
                try:
                    conn.execute("BEGIN TRANSACTION")
                    for item_data in batch:
                        queries, future = item_data
                        try:
                            # Main query
                            main_sql, main_params = queries[0]
                            cursor = conn.execute(main_sql, main_params)
                            res = (
                                cursor.lastrowid
                                if cursor.lastrowid is not None
                                else cursor.rowcount
                            )
                            # Subsidiary queries (updates)
                            for sql, params in queries[1:]:
                                conn.execute(sql, params)
                            results.append((future, res, None))
                        except Exception as e:
                            # Log and capture exception for this specific task
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

    def _execute_write(self, queries: list[tuple[str, tuple[Any, ...]]], sync: bool = True) -> Any:
        """Helper to either enqueue a write or execute it directly."""
        if not self.use_queue:
            with self.get_connection() as conn:
                try:
                    main_sql, main_params = queries[0]
                    cursor = conn.execute(main_sql, main_params)
                    res = cursor.lastrowid if cursor.lastrowid is not None else cursor.rowcount
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

    # --- User Management ---

    def create_user(self, username: str, bio: str = "", sync: bool = True) -> Any:
        """Register a new user."""
        try:
            # Fallback to direct read check if sync fails, but IntegrityError is fine
            queries = [
                (
                    "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
                    (username, bio, time.time()),
                )
            ]
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return self.get_user_id(username)

    def get_user_id(self, username: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            return row["id"] if row else None

    # Separate profile viewing function as requested explicitly!
    def view_profile(self, username: str) -> dict[str, Any] | None:
        """View a user's full profile including basic stats."""
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return dict(row)
            return None

    def search_users(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search for users by username or bio."""
        with self.get_connection() as conn:
            search_term = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM users WHERE username LIKE ? OR bio LIKE ? LIMIT ?",
                (search_term, search_term, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def search_posts(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search for posts by content."""
        with self.get_connection() as conn:
            search_term = f"%{query}%"
            rows = conn.execute(
                """
                SELECT p.*, u.username
                FROM posts p
                JOIN users u ON p.user_id = u.id
                WHERE p.content LIKE ?
                ORDER BY p.id DESC
                LIMIT ?
                """,
                (search_term, limit),
            ).fetchall()
            return self._parse_posts(rows)

    # --- Actions ---

    def create_post(
        self, username: str, content: str, reply_to_id: int | None = None, sync: bool = True
    ) -> Any:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        queries = [
            (
                "INSERT INTO posts (user_id, content, created_at, type, reply_to_id) VALUES (?, ?, ?, 'post', ?)",
                (user_id, content, time.time(), reply_to_id),
            ),
            ("UPDATE users SET posts_count = posts_count + 1 WHERE id = ?", (user_id,)),
        ]
        if reply_to_id:
            queries.append(
                ("UPDATE posts SET reply_count = reply_count + 1 WHERE id = ?", (reply_to_id,))
            )
            # Notification for reply
            with self.get_connection() as conn:
                target_user = conn.execute(
                    "SELECT user_id FROM posts WHERE id = ?", (reply_to_id,)
                ).fetchone()
                if target_user and target_user["user_id"] != user_id:
                    queries.append(
                        (
                            "INSERT INTO activities (target_user_id, source_user_id, action_type, post_id, created_at) VALUES (?, ?, 'reply', ?, ?)",
                            (target_user["user_id"], user_id, reply_to_id, time.time()),
                        )
                    )

        return self._execute_write(queries, sync=sync)

    def repost(self, username: str, post_id: int, sync: bool = True) -> Any:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        with self.get_connection() as conn:
            orig = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not orig:
                raise ValueError("Post not found")

        queries = [
            (
                "INSERT INTO posts (user_id, content, created_at, type, quote_of_id) VALUES (?, ?, ?, 'repost', ?)",
                (user_id, "", time.time(), post_id),
            ),
            ("UPDATE posts SET reposts_count = reposts_count + 1 WHERE id = ?", (post_id,)),
            ("UPDATE users SET posts_count = posts_count + 1 WHERE id = ?", (user_id,)),
        ]

        # Notification for repost
        if orig["user_id"] != user_id:
            queries.append(
                (
                    "INSERT INTO activities (target_user_id, source_user_id, action_type, post_id, created_at) VALUES (?, ?, 'repost', ?, ?)",
                    (orig["user_id"], user_id, post_id, time.time()),
                )
            )

        return self._execute_write(queries, sync=sync)

    def quote_repost(self, username: str, post_id: int, content: str, sync: bool = True) -> Any:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        with self.get_connection() as conn:
            orig = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not orig:
                raise ValueError("Post not found")

        queries = [
            (
                "INSERT INTO posts (user_id, content, created_at, type, quote_of_id) VALUES (?, ?, ?, 'quote', ?)",
                (user_id, content, time.time(), post_id),
            ),
            ("UPDATE posts SET reposts_count = reposts_count + 1 WHERE id = ?", (post_id,)),
            ("UPDATE users SET posts_count = posts_count + 1 WHERE id = ?", (user_id,)),
        ]

        if orig["user_id"] != user_id:
            queries.append(
                (
                    "INSERT INTO activities (target_user_id, source_user_id, action_type, post_id, created_at) VALUES (?, ?, 'quote', ?, ?)",
                    (orig["user_id"], user_id, post_id, time.time()),
                )
            )

        return self._execute_write(queries, sync=sync)

    def like(self, username: str, post_id: int, sync: bool = True):
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        with self.get_connection() as conn:
            orig = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not orig:
                raise ValueError("Post not found")

        queries = [
            (
                "INSERT INTO likes (user_id, post_id, created_at) VALUES (?, ?, ?)",
                (user_id, post_id, time.time()),
            ),
            ("UPDATE posts SET likes_count = likes_count + 1 WHERE id = ?", (post_id,)),
        ]

        if orig["user_id"] != user_id:
            queries.append(
                (
                    "INSERT INTO activities (target_user_id, source_user_id, action_type, post_id, created_at) VALUES (?, ?, 'like', ?, ?)",
                    (orig["user_id"], user_id, post_id, time.time()),
                )
            )

        try:
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return False  # Already liked

    def unlike(self, username: str, post_id: int, sync: bool = True):
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        queries = [
            ("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id)),
            ("UPDATE posts SET likes_count = likes_count - 1 WHERE id = ?", (post_id,)),
        ]
        # In an advanced implementation we'd also reverse the activity, but this is fine for now
        return self._execute_write(queries, sync=sync)

    def follow(self, username: str, target_username: str, sync: bool = True):
        follower_id = self.get_user_id(username)
        followee_id = self.get_user_id(target_username)
        if not follower_id or not followee_id:
            raise ValueError("User not found")
        if follower_id == followee_id:
            return False

        queries = [
            (
                "INSERT INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
                (follower_id, followee_id, time.time()),
            ),
            ("UPDATE users SET following_count = following_count + 1 WHERE id = ?", (follower_id,)),
            ("UPDATE users SET followers_count = followers_count + 1 WHERE id = ?", (followee_id,)),
            (
                "INSERT INTO activities (target_user_id, source_user_id, action_type, created_at) VALUES (?, ?, 'follow', ?)",
                (followee_id, follower_id, time.time()),
            ),
        ]

        try:
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return False

    def unfollow(self, username: str, target_username: str, sync: bool = True):
        follower_id = self.get_user_id(username)
        followee_id = self.get_user_id(target_username)
        if not follower_id or not followee_id:
            raise ValueError("User not found")

        queries = [
            (
                "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
                (follower_id, followee_id),
            ),
            ("UPDATE users SET following_count = following_count - 1 WHERE id = ?", (follower_id,)),
            ("UPDATE users SET followers_count = followers_count - 1 WHERE id = ?", (followee_id,)),
        ]
        return self._execute_write(queries, sync=sync)

    def block(self, username: str, target_username: str, sync: bool = True):
        blocker_id = self.get_user_id(username)
        blocked_id = self.get_user_id(target_username)
        if not blocker_id or not blocked_id:
            raise ValueError("User not found")

        queries = [
            (
                "INSERT INTO blocks (blocker_id, blocked_id, created_at) VALUES (?, ?, ?)",
                (blocker_id, blocked_id, time.time()),
            ),
            # Delete follows
            (
                "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
                (blocker_id, blocked_id),
            ),
            (
                "DELETE FROM follows WHERE follower_id = ? AND followee_id = ?",
                (blocked_id, blocker_id),
            ),
        ]

        try:
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return False

    def unblock(self, username: str, target_username: str, sync: bool = True):
        blocker_id = self.get_user_id(username)
        blocked_id = self.get_user_id(target_username)
        if not blocker_id or not blocked_id:
            raise ValueError("User not found")

        queries = [
            ("DELETE FROM blocks WHERE blocker_id = ? AND blocked_id = ?", (blocker_id, blocked_id))
        ]
        return self._execute_write(queries, sync=sync)

    # --- Direct Messages ---

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

            # Mark as read
            unread_ids = [r["id"] for r in rows if r["receiver_id"] == user_id and not r["read"]]
            if unread_ids:
                placeholders = ",".join(["?"] * len(unread_ids))
                conn.execute(
                    f"UPDATE direct_messages SET read = 1 WHERE id IN ({placeholders})", unread_ids
                )
                conn.commit()

            return [dict(r) for r in rows]

    # --- Notifications / Activities ---

    def view_activities(self, username: str, limit: int = 50) -> list[dict[str, Any]]:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")

        with self.get_connection() as conn:
            rows = conn.execute(
                """
                SELECT a.*, u.username as source_username, p.content as post_content
                FROM activities a
                JOIN users u ON a.source_user_id = u.id
                LEFT JOIN posts p ON a.post_id = p.id
                WHERE a.target_user_id = ?
                ORDER BY a.created_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()

            # Mark as read
            unread_ids = [r["id"] for r in rows if not r["read"]]
            if unread_ids:
                placeholders = ",".join(["?"] * len(unread_ids))
                conn.execute(
                    f"UPDATE activities SET read = 1 WHERE id IN ({placeholders})", unread_ids
                )
                conn.commit()

            return [dict(r) for r in rows]

    # --- Timelines and Feeds ---

    def _parse_posts(self, rows) -> list[dict]:
        posts = []
        for row in rows:
            post = Post(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                content=row["content"],
                created_at=row["created_at"],
                likes_count=row["likes_count"],
                reposts_count=row["reposts_count"],
                reply_count=row["reply_count"],
                type=row["type"],
                reply_to_id=row["reply_to_id"],
                quote_of_id=row["quote_of_id"],
                formatted_date=time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])
                ),
            )
            posts.append(post.to_dict())
        return posts

    def get_posts_by_ids(self, post_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Return a map of post_id -> post dict for the requested IDs."""
        normalized = sorted({int(pid) for pid in post_ids if pid is not None})
        if not normalized:
            return {}

        placeholders = ",".join("?" for _ in normalized)
        query = f"""
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.id IN ({placeholders})
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, normalized).fetchall()
            return {post["id"]: post for post in self._parse_posts(rows)}

    # Separation of Feeds using a router pattern!
    def get_feed(
        self, feed_type: str, username: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Generic feed router. Returns dict with 'posts' and 'next_cursor'"""
        if feed_type == "chronological_home":
            assert username is not None, "username required for chronological_home feed"
            return self._feed_chronological_home(username, **kwargs)
        if feed_type == "profile":
            assert username is not None, "username required for profile feed"
            return self._feed_profile(username, **kwargs)
        if feed_type == "curated_global":
            return self._feed_curated_global(username or "", **kwargs)
        if feed_type == "firehose":
            return self._feed_firehose(**kwargs)
        raise ValueError(f"Unknown feed_type: {feed_type}")

    def _feed_chronological_home(
        self, username: str, limit: int = 20, cursor: int | None = None
    ) -> dict[str, Any]:
        """Chronological Home feed of follows."""
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        query = """
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id IN (
                SELECT followee_id FROM follows WHERE follower_id = ?
            )
            AND (? IS NULL OR p.id < ?)
            AND p.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (user_id, cursor, cursor, user_id, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def _feed_profile(
        self, username: str, limit: int = 20, cursor: int | None = None
    ) -> dict[str, Any]:
        """Chronological feed for a specific user."""
        target_id = self.get_user_id(username)
        if not target_id:
            return {"posts": [], "next_cursor": None}

        query = """
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE p.user_id = ?
            AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (target_id, cursor, cursor, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def _feed_curated_global(
        self, username: str, limit: int = 20, cursor: int | None = None
    ) -> dict[str, Any]:
        """
        Curated Global: A mix of trending posts and posts from people you follow.
        For simulation, we define trending as having >1 likes or reposts, mixed with recent.
        """
        user_id = self.get_user_id(username) if username else -1

        query = """
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE (? IS NULL OR p.id < ?)
            AND p.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = ?)
            -- Filter out low-engagement old posts unless it's someone we follow
            AND (
                p.likes_count > 0
                OR p.reposts_count > 0
                OR p.user_id IN (SELECT followee_id FROM follows WHERE follower_id = ?)
            )
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (cursor, cursor, user_id, user_id, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def _feed_firehose(self, limit: int = 20, cursor: int | None = None) -> dict[str, Any]:
        """Global firehose: everything."""
        query = """
            SELECT p.*, u.username
            FROM posts p
            JOIN users u ON p.user_id = u.id
            WHERE (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (cursor, cursor, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    # ================================================================ #
    # Timeline Selection Strategies
    # ================================================================ #

    # Available timeline strategies that agents can use
    TIMELINE_STRATEGIES = {
        "follower_chronological": {
            "description": "Chronological feed from followed users only",
            "internal_feed": "chronological_home",
        },
        "pure_recsys": {
            "description": "Pure recommendation algorithm feed",
            "internal_feed": None,  # Uses get_recommendations()
        },
        "hybrid_recsys_follower": {
            "description": "Hybrid: recommendations + followed users feed (configurable split)",
            "internal_feed": None,  # Uses both
        },
        "curated_global": {
            "description": "Global curated feed (trending + network mix)",
            "internal_feed": "curated_global",
        },
    }

    def get_timeline(
        self,
        strategy: str,
        username: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: Any,
    ) -> list[dict]:
        """Get timeline using specified strategy.

        Args:
            strategy: Timeline strategy name (see TIMELINE_STRATEGIES)
            username: User to get timeline for
            limit: Total posts to return
            recsys_type: Optional recommendation algorithm override.
            **timeline_config: Strategy-specific config (e.g., recsys_ratio, fallback_posts)

        Returns
        -------
            List of posts ordered by strategy
        """
        if strategy == "follower_chronological":
            feed = self.get_feed("chronological_home", username, limit=limit)
            return feed.get("posts", [])

        if strategy == "pure_recsys":
            # Pure recommendations only
            return self.get_recommendations(username, limit, recsys_type=recsys_type)

        if strategy == "hybrid_recsys_follower":
            # Blend recommendations and followed users with configurable ratio
            recsys_ratio = timeline_config.get("recsys_ratio", 0.6)
            follower_ratio = timeline_config.get("follower_ratio", 0.4)

            rec_count = max(1, int(limit * recsys_ratio))
            follower_count = max(1, int(limit * follower_ratio))

            rec_posts = self.get_recommendations(
                username,
                rec_count,
                recsys_type=recsys_type,
            )
            follow_feed = self.get_feed("chronological_home", username, limit=follower_count)
            follow_posts = follow_feed.get("posts", [])

            # Merge and deduplicate
            seen_ids = set()
            combined = []

            # Add recsys first (higher priority)
            for post in rec_posts or []:
                post_id = post.get("id", post.get("post_id"))
                if post_id not in seen_ids:
                    combined.append(post)
                    seen_ids.add(post_id)

            # Then add followed posts
            for post in follow_posts:
                post_id = post.get("id", post.get("post_id"))
                if post_id not in seen_ids:
                    combined.append(post)
                    seen_ids.add(post_id)

            return combined[:limit]

        if strategy == "curated_global":
            feed = self.get_feed("curated_global", username, limit=limit)
            return feed.get("posts", [])

        # Fallback: use follower_chronological
        logger.warning(
            f"Unknown timeline strategy '{strategy}', falling back to follower_chronological"
        )
        return self.get_timeline("follower_chronological", username, limit, **timeline_config)

    # ================================================================ #
    # OASIS-Compatible Extended Methods
    # ================================================================ #

    def dislike_post(self, username: str, post_id: int) -> bool:
        """Add a dislike (negative reaction) to a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if already disliked
                cursor = conn.execute(
                    "SELECT 1 FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if cursor.fetchone():
                    return False

                # Add dislike
                now = time.time()
                conn.execute(
                    "INSERT INTO dislikes (user_id, post_id, created_at) VALUES (?, ?, ?)",
                    (user_id, post_id, now),
                )
                # Update post dislike count
                conn.execute(
                    "UPDATE posts SET dislikes_count = dislikes_count + 1 WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error disliking post {post_id}: {e}")
            return False

    def unlike_post(self, username: str, post_id: int) -> bool:
        """Remove a like from a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if like exists
                cursor = conn.execute(
                    "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if not cursor.fetchone():
                    return False

                # Remove like
                conn.execute(
                    "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                # Decrement like count
                conn.execute(
                    "UPDATE posts SET likes_count = MAX(0, likes_count - 1) WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error unliking post {post_id}: {e}")
            return False

    def undo_dislike_post(self, username: str, post_id: int) -> bool:
        """Remove a dislike from a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if dislike exists
                cursor = conn.execute(
                    "SELECT 1 FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if not cursor.fetchone():
                    return False

                # Remove dislike
                conn.execute(
                    "DELETE FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                # Decrement dislike count
                conn.execute(
                    "UPDATE posts SET dislikes_count = MAX(0, dislikes_count - 1) WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error undoing dislike for post {post_id}: {e}")
            return False

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

    def get_trending_posts(self, limit: int = 10, days: int = 7) -> list[dict]:
        """Get trending posts based on engagement."""
        try:
            with self.get_connection() as conn:
                cutoff = time.time() - (days * 86400)
                cursor = conn.execute(
                    """
                    SELECT p.*, u.username,
                           (p.likes_count + p.reposts_count) as engagement
                    FROM posts p
                    JOIN users u ON p.user_id = u.id
                    WHERE p.created_at > ?
                    ORDER BY engagement DESC, p.created_at DESC
                    LIMIT ?
                """,
                    (cutoff, limit),
                )

                posts = []
                for row in cursor.fetchall():
                    posts.append(
                        {
                            "id": row[0],
                            "user_id": row[1],
                            "content": row[3],
                            "created_at": row[4],
                            "likes_count": row[7],
                            "reposts_count": row[8],
                            "username": row[-2],
                            "engagement_score": row[-1],
                        }
                    )
                return posts
        except Exception as e:
            logger.error(f"Error getting trending posts: {e}")
            return []

    # ================================================================ #
    # Recommendation System (OASIS-compatible)
    # ================================================================ #

    def init_recsys(
        self,
        recsys_type: str = "twitter",
        user_context_recent_posts: int = 0,
        include_like_trace: bool = False,
        like_trace_window: int = 5,
        like_trace_weight: float = 0.0,
        include_like_trace_in_context: bool = True,
    ) -> None:
        """Initialize recommendation system for a specific algorithm type.

        Args:
            recsys_type: Algorithm type ("twitter", "twitter_tfidf", or "twhin").
                        Can be called multiple times to enable multiple algorithms simultaneously.
            user_context_recent_posts: Number of recent authored posts to add to user context.
            include_like_trace: Whether to include like-trace signals in ranking.
            like_trace_window: Number of recent liked posts to consider per user.
            like_trace_weight: Blending weight in [0, 1] for like-trace similarity.
            include_like_trace_in_context: Whether liked post snippets are appended to
                textual user context.
        """
        recsys_type = str(recsys_type or "").strip().lower()
        if recsys_type not in self.SUPPORTED_RECSYS_TYPES:
            raise ValueError(
                "Unsupported recsys_type for Twitter-like backend: "
                f"'{recsys_type}'. Supported: {sorted(self.SUPPORTED_RECSYS_TYPES)}"
            )

        user_context_recent_posts = max(0, int(user_context_recent_posts or 0))
        like_trace_window = max(0, int(like_trace_window or 0))
        like_trace_weight = max(0.0, min(1.0, float(like_trace_weight or 0.0)))
        include_like_trace = bool(include_like_trace)
        include_like_trace_in_context = bool(include_like_trace_in_context)

        # Initialize dict on first call
        if not hasattr(self, "_recsys_types"):
            self._recsys_types: dict[str, dict] = {}

        # Add this algorithm type to the set (cumulative, not overwriting)
        self._recsys_types[recsys_type] = {
            "type": recsys_type,
            "backend": None,
            "embeddings_cache": {},
            "model": None,
            "tokenizer": None,
            "user_context_recent_posts": user_context_recent_posts,
            "include_like_trace": include_like_trace,
            "like_trace_window": like_trace_window,
            "like_trace_weight": like_trace_weight,
            "include_like_trace_in_context": include_like_trace_in_context,
        }

        state = self._recsys_types[recsys_type]

        if recsys_type == "twitter_tfidf":
            state["backend"] = "tfidf"
            logger.info("Initialized %s recsys backend using TF-IDF", recsys_type)

        elif recsys_type == "twitter":
            state["backend"] = "sentence_transformer"
            state["model"] = self._load_sentence_transformer_model(
                model_name="paraphrase-MiniLM-L6-v2",
                recsys_type=recsys_type,
            )

        elif recsys_type == "twhin":
            # Strict TWHIN mode: only the canonical model is allowed.
            tokenizer, model = self._load_twhin_transformers_model()
            state["backend"] = "twhin_transformers"
            state["tokenizer"] = tokenizer
            state["model"] = model

        logger.info(
            "Initialized recsys type '%s'. Active types: %s. Context cfg: "
            "recent_posts=%s include_like_trace=%s like_trace_window=%s "
            "like_trace_weight=%.3f include_like_trace_in_context=%s",
            recsys_type,
            list(self._recsys_types.keys()),
            user_context_recent_posts,
            include_like_trace,
            like_trace_window,
            like_trace_weight,
            include_like_trace_in_context,
        )

    def _fetch_recent_posts_by_user(
        self,
        conn: sqlite3.Connection,
        user_ids: list[int],
        limit_per_user: int,
    ) -> dict[int, list[str]]:
        """Fetch recent authored post contents for each user."""
        if not user_ids or limit_per_user <= 0:
            return {}

        placeholders = ",".join("?" for _ in user_ids)
        rows = conn.execute(
            f"""
            SELECT user_id, content
            FROM posts
            WHERE user_id IN ({placeholders}) AND type != 'repost'
            ORDER BY user_id ASC, created_at DESC, id DESC
            """,
            tuple(user_ids),
        ).fetchall()

        result: dict[int, list[str]] = {}
        for row in rows:
            uid = int(row[0])
            content = str(row[1] or "").strip()
            if not content:
                continue
            bucket = result.setdefault(uid, [])
            if len(bucket) < limit_per_user:
                bucket.append(content)
        return result

    def _fetch_liked_posts_by_user(
        self,
        conn: sqlite3.Connection,
        user_ids: list[int],
        limit_per_user: int,
    ) -> dict[int, list[str]]:
        """Fetch recently liked post contents for each user."""
        if not user_ids or limit_per_user <= 0:
            return {}

        placeholders = ",".join("?" for _ in user_ids)
        rows = conn.execute(
            f"""
            SELECT l.user_id, p.content
            FROM likes l
            JOIN posts p ON p.id = l.post_id
            WHERE l.user_id IN ({placeholders})
            ORDER BY l.user_id ASC, l.created_at DESC, l.post_id DESC
            """,
            tuple(user_ids),
        ).fetchall()

        result: dict[int, list[str]] = {}
        for row in rows:
            uid = int(row[0])
            content = str(row[1] or "").strip()
            if not content:
                continue
            bucket = result.setdefault(uid, [])
            if len(bucket) < limit_per_user:
                bucket.append(content)
        return result

    def _generate_user_context(
        self,
        user: dict[str, Any],
        *,
        max_recent_posts: int,
        include_like_trace: bool,
        include_like_trace_in_context: bool,
        like_trace_window: int,
    ) -> tuple[str, list[str]]:
        """Build a user context string from bio, recent posts, and optional like trace."""
        bio = str(user.get("bio") or "").strip() or "No bio provided."

        recent_posts = [
            str(text).strip()
            for text in (user.get("recent_posts") or [])[: max(0, max_recent_posts)]
            if str(text).strip()
        ]
        liked_posts = [
            str(text).strip()
            for text in (user.get("liked_posts") or [])[: max(0, like_trace_window)]
            if str(text).strip()
        ]

        lines = [f"User bio: {bio}"]
        if recent_posts:
            lines.append("Recent authored posts: " + " | ".join(recent_posts))
        if include_like_trace and include_like_trace_in_context and liked_posts:
            lines.append("Recently liked posts: " + " | ".join(liked_posts))

        return "\n".join(lines), liked_posts

    def _resolve_context_options(self, recsys_state: dict | None) -> dict[str, Any]:
        """Normalize optional context and like-trace options from recsys state."""
        if recsys_state is None:
            return {
                "context_recent_posts": 0,
                "include_like_trace": False,
                "like_trace_window": 0,
                "like_trace_weight": 0.0,
                "include_like_trace_in_context": True,
            }

        return {
            "context_recent_posts": int(recsys_state.get("user_context_recent_posts", 0) or 0),
            "include_like_trace": bool(recsys_state.get("include_like_trace", False)),
            "like_trace_window": int(recsys_state.get("like_trace_window", 0) or 0),
            "like_trace_weight": max(
                0.0,
                min(1.0, float(recsys_state.get("like_trace_weight", 0.0) or 0.0)),
            ),
            "include_like_trace_in_context": bool(
                recsys_state.get("include_like_trace_in_context", True)
            ),
        }

    def _build_user_context_index(
        self,
        users: list[dict[str, Any]],
        *,
        context_recent_posts: int,
        include_like_trace: bool,
        include_like_trace_in_context: bool,
        like_trace_window: int,
    ) -> dict[int, dict[str, Any]]:
        """Compute user context text and like traces once per update pass."""
        index: dict[int, dict[str, Any]] = {}
        for user in users:
            user_id = int(user["id"])
            context, liked_posts = self._generate_user_context(
                user,
                max_recent_posts=context_recent_posts,
                include_like_trace=include_like_trace,
                include_like_trace_in_context=include_like_trace_in_context,
                like_trace_window=like_trace_window,
            )
            index[user_id] = {
                "context": context,
                "liked_posts": liked_posts,
            }
        return index

    def _context_cache_key(
        self,
        user_id: int,
        user_context: str,
        *,
        context_recent_posts: int,
        include_like_trace: bool,
        like_trace_window: int,
        include_like_trace_in_context: bool,
    ) -> tuple[Any, ...]:
        """Build stable cache key for user context embeddings."""
        return (
            "user",
            user_id,
            user_context,
            context_recent_posts,
            include_like_trace,
            like_trace_window,
            include_like_trace_in_context,
        )

    def _blend_with_like_trace(
        self,
        *,
        base_sims: Any,
        liked_posts: list[str],
        like_trace_weight: float,
        include_like_trace: bool,
        encode_texts: Any,
        score_profile: Any,
    ) -> Any:
        """Blend base user-context scores with like-trace profile scores."""
        if not include_like_trace or like_trace_weight <= 0.0 or not liked_posts:
            return base_sims

        liked_embeddings = encode_texts(liked_posts, 16)
        like_profile = liked_embeddings.mean(dim=0)
        like_sims = score_profile(like_profile)
        return (1.0 - like_trace_weight) * base_sims + like_trace_weight * like_sims

    def _top_post_ids_for_user(
        self,
        *,
        user_id: int,
        posts: list[dict[str, Any]],
        scores: Any,
        max_posts: int,
    ) -> list[int]:
        """Sort candidate posts by score and return top-k non-self post ids."""
        scored: list[tuple[int, float]] = []
        for idx, post in enumerate(posts):
            if int(post["user_id"]) == int(user_id):
                continue
            scored.append((int(post["id"]), float(scores[idx])))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [post_id for post_id, _ in scored[:max_posts]]

    def _load_sentence_transformer_model(
        self,
        model_name: str,
        recsys_type: str,
    ) -> Any | None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning(
                "sentence-transformers not available for %s; embeddings disabled",
                recsys_type,
            )
            return None

        try:
            loaded_model = SentenceTransformer(model_name)
            logger.info(
                "Loaded %s recsys model '%s'",
                recsys_type,
                model_name,
            )
            return loaded_model
        except Exception as err:
            logger.warning(
                "Failed loading %s recsys model '%s': %s",
                recsys_type,
                model_name,
                err,
            )
            return None

    def _load_twhin_transformers_model(self) -> tuple[Any, Any]:
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer

            device = "cuda" if torch.cuda.is_available() else "cpu"
            model_name = "Twitter/twhin-bert-base"
            local_model_path = (
                os.getenv("TWHIN_MODEL_PATH")
                or os.getenv("TWITTER_TWHIN_MODEL_PATH")
                or os.getenv("HF_TWHIN_MODEL_PATH")
            )
            model_source = local_model_path.strip() if isinstance(local_model_path, str) else ""
            if not model_source:
                model_source = model_name

            tokenizer = AutoTokenizer.from_pretrained(model_source)
            model = AutoModel.from_pretrained(model_source).to(device)
            model.eval()
            logger.info(
                "Loaded twhin recsys model '%s' (source=%s) on %s", model_name, model_source, device
            )
            return tokenizer, model
        except Exception as err:
            raise RuntimeError(
                f"Failed loading strict TWHIN model 'Twitter/twhin-bert-base': {err}"
            ) from err

    def update_recommendations(
        self, active_user_ids: list[int] | None = None, max_posts: int = 10
    ) -> None:
        """Update recommendations for all initialized algorithm types.

        Args:
            active_user_ids: Optional list of user IDs to limit updates to.
            max_posts: Maximum recommendations per user per algorithm.

        Generates recommendations for each initialized recsys type and stores them
        in the database with the algorithm type tagged.
        """
        if not hasattr(self, "_recsys_types") or not self._recsys_types:
            logger.debug("No recsys types initialized; skipping recommendations update")
            return

        try:
            with self.get_connection() as conn:
                # Get users
                if active_user_ids:
                    placeholders = ",".join("?" * len(active_user_ids))
                    cursor = conn.execute(
                        f"SELECT id, username, bio FROM users WHERE id IN ({placeholders})",
                        active_user_ids,
                    )
                else:
                    cursor = conn.execute("SELECT id, username, bio FROM users")

                users = [{"id": r[0], "username": r[1], "bio": r[2]} for r in cursor.fetchall()]
                user_ids = [int(user["id"]) for user in users]

                # Get recent posts
                cursor = conn.execute(
                    "SELECT id, user_id, content, created_at, likes_count FROM posts WHERE type != 'repost' ORDER BY created_at DESC LIMIT 1000"
                )
                posts = [
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "content": r[2],
                        "created_at": r[3],
                        "likes": r[4],
                    }
                    for r in cursor.fetchall()
                ]

                if not users or not posts:
                    logger.debug("No users or posts found; skipping recommendations update")
                    return

                max_recent_posts = max(
                    int(state.get("user_context_recent_posts", 0) or 0)
                    for state in self._recsys_types.values()
                )
                max_like_window = max(
                    (
                        int(state.get("like_trace_window", 0) or 0)
                        for state in self._recsys_types.values()
                        if bool(state.get("include_like_trace", False))
                    ),
                    default=0,
                )

                recent_posts_by_user = self._fetch_recent_posts_by_user(
                    conn,
                    user_ids,
                    max_recent_posts,
                )
                liked_posts_by_user = self._fetch_liked_posts_by_user(
                    conn,
                    user_ids,
                    max_like_window,
                )
                for user in users:
                    uid = int(user["id"])
                    user["recent_posts"] = recent_posts_by_user.get(uid, [])
                    user["liked_posts"] = liked_posts_by_user.get(uid, [])

                # Clear old recommendations (once per update)
                conn.execute("DELETE FROM recommendations")

                # Generate recommendations for each algorithm type
                for recsys_type, state in self._recsys_types.items():
                    logger.debug(f"Computing {recsys_type} recommendations for {len(users)} users")

                    if recsys_type in ("twitter", "twhin", "twitter_tfidf"):
                        rec_matrix = self._rec_embedding(users, posts, max_posts, state)
                    else:
                        logger.warning(f"Unknown recsys_type '{recsys_type}'; skipping")
                        continue

                    # Store recommendations with algorithm type
                    for user_id, post_ids in rec_matrix.items():
                        for post_id in post_ids:
                            conn.execute(
                                "INSERT OR IGNORE INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
                                (user_id, post_id, recsys_type),
                            )

                    logger.info(
                        f"Updated {recsys_type} recommendations for {len(rec_matrix)} users"
                    )

                conn.commit()
                logger.info(
                    f"Recommendations update complete for {len(self._recsys_types)} algorithm types"
                )
        except Exception as e:
            logger.error(f"Error updating recommendations: {e}", exc_info=True)

    def _rec_embedding(
        self, users: list, posts: list, max_posts: int, recsys_state: dict | None = None
    ) -> dict:
        """Embedding-based recommendations.

        Args:
            users: List of user dicts.
            posts: List of post dicts.
            max_posts: Maximum recommendations per user.
            recsys_state: State dict for this algorithm type containing 'model' and 'embeddings_cache'.
                         If None, attempts to use legacy self.recsys_model.
        """
        # Handle backward compatibility (single-model mode)
        if recsys_state is None:
            if not hasattr(self, "recsys_model") or self.recsys_model is None:
                return {}
            model = self.recsys_model
            embeddings_cache = getattr(self, "embeddings_cache", {})
        else:
            model = recsys_state.get("model")
            embeddings_cache = recsys_state.get("embeddings_cache", {})
            backend = str(recsys_state.get("backend") or "sentence_transformer").strip()

        options = self._resolve_context_options(recsys_state)
        context_recent_posts = int(options["context_recent_posts"])
        include_like_trace = bool(options["include_like_trace"])
        like_trace_window = int(options["like_trace_window"])
        like_trace_weight = float(options["like_trace_weight"])
        include_like_trace_in_context = bool(options["include_like_trace_in_context"])

        user_context_index = self._build_user_context_index(
            users,
            context_recent_posts=context_recent_posts,
            include_like_trace=include_like_trace,
            include_like_trace_in_context=include_like_trace_in_context,
            like_trace_window=like_trace_window,
        )

        if recsys_state is not None and backend == "tfidf":
            return self._rec_tfidf(users, posts, max_posts, user_context_index=user_context_index)

        if recsys_state is not None and backend == "twhin_transformers":
            tokenizer = recsys_state.get("tokenizer")
            return self._rec_twhin(
                users,
                posts,
                max_posts,
                tokenizer,
                model,
                embeddings_cache,
                user_context_index=user_context_index,
                include_like_trace=include_like_trace,
                like_trace_weight=like_trace_weight,
                context_recent_posts=context_recent_posts,
                like_trace_window=like_trace_window,
                include_like_trace_in_context=include_like_trace_in_context,
            )

        if model is None:
            return {}

        try:
            rec_matrix = {}

            # Encode posts (batch)
            post_texts = [p["content"] for p in posts]
            post_embeddings = model.encode(post_texts, batch_size=32, convert_to_tensor=True)

            # For each user
            for user in users:
                user_id = int(user["id"])
                context_bundle = user_context_index[user_id]
                user_context = str(context_bundle["context"])
                liked_posts = list(context_bundle["liked_posts"])

                # Get or compute user embedding
                cache_key = self._context_cache_key(
                    user_id,
                    user_context,
                    context_recent_posts=context_recent_posts,
                    include_like_trace=include_like_trace,
                    like_trace_window=like_trace_window,
                    include_like_trace_in_context=include_like_trace_in_context,
                )
                if cache_key not in embeddings_cache:
                    user_emb = model.encode(user_context, convert_to_tensor=True)
                    embeddings_cache[cache_key] = user_emb
                else:
                    user_emb = embeddings_cache[cache_key]

                # Compute similarities
                import torch

                sims = torch.nn.functional.cosine_similarity(user_emb.unsqueeze(0), post_embeddings)
                sims = self._blend_with_like_trace(
                    base_sims=sims,
                    liked_posts=liked_posts,
                    like_trace_weight=like_trace_weight,
                    include_like_trace=include_like_trace,
                    encode_texts=lambda texts, batch_size: model.encode(
                        texts,
                        batch_size=batch_size,
                        convert_to_tensor=True,
                    ),
                    score_profile=lambda like_profile: torch.nn.functional.cosine_similarity(
                        like_profile.unsqueeze(0),
                        post_embeddings,
                    ),
                )

                rec_matrix[user_id] = self._top_post_ids_for_user(
                    user_id=user_id,
                    posts=posts,
                    scores=sims,
                    max_posts=max_posts,
                )

            return rec_matrix
        except Exception as e:
            logger.error(f"Error in embedding recsys: {e}", exc_info=True)
            return {}

    def _rec_tfidf(
        self,
        users: list,
        posts: list,
        max_posts: int,
        user_context_index: dict[int, dict[str, Any]] | None = None,
    ) -> dict:
        """TF-IDF baseline recommender: bio-to-post cosine similarity."""
        rec_matrix: dict[int, list[int]] = {}
        if not users or not posts:
            return rec_matrix

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            if user_context_index is None:
                user_texts = [str(user.get("bio") or "") for user in users]
            else:
                user_texts = [str(user_context_index[int(user["id"])]["context"]) for user in users]
            post_texts = [str(post.get("content") or "") for post in posts]
            corpus = user_texts + post_texts

            vectorizer = TfidfVectorizer()
            matrix = vectorizer.fit_transform(corpus)
            user_vectors = matrix[: len(users)]
            post_vectors = matrix[len(users) :]
            similarities = cosine_similarity(user_vectors, post_vectors)

            for user_index, user in enumerate(users):
                user_id = int(user["id"])
                rec_matrix[user_id] = self._top_post_ids_for_user(
                    user_id=user_id,
                    posts=posts,
                    scores=similarities[user_index],
                    max_posts=max_posts,
                )

            return rec_matrix
        except ValueError:
            # Empty-vocabulary case (e.g., all bios/posts blank): fallback to recency.
            for user in users:
                user_id = int(user["id"])
                rec_matrix[user_id] = [
                    int(post["id"]) for post in posts if int(post["user_id"]) != user_id
                ][:max_posts]
            return rec_matrix
        except Exception as e:
            logger.error("Error in TF-IDF recsys: %s", e, exc_info=True)
            return {}

    def _rec_twhin(
        self,
        users: list,
        posts: list,
        max_posts: int,
        tokenizer: Any,
        model: Any,
        embeddings_cache: dict,
        user_context_index: dict[int, dict[str, Any]],
        include_like_trace: bool = False,
        like_trace_weight: float = 0.0,
        context_recent_posts: int = 0,
        like_trace_window: int = 0,
        include_like_trace_in_context: bool = True,
    ) -> dict:
        """TWHIN-BERT recommendations via transformers tokenizer/model."""
        if tokenizer is None or model is None:
            return {}

        try:
            import torch

            rec_matrix: dict[int, list[int]] = {}

            device = next(model.parameters()).device

            def _encode_texts(texts: list[str], batch_size: int = 32) -> torch.Tensor:
                batches: list[torch.Tensor] = []
                for start in range(0, len(texts), batch_size):
                    batch_texts = texts[start : start + batch_size]
                    tokens = tokenizer(
                        batch_texts,
                        padding=True,
                        truncation=True,
                        max_length=256,
                        return_tensors="pt",
                    ).to(device)
                    with torch.no_grad():
                        outputs = model(**tokens)
                        hidden = outputs.last_hidden_state
                        mask = tokens["attention_mask"].unsqueeze(-1).float()
                        pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1e-9)
                        pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
                    batches.append(pooled.cpu())
                return torch.cat(batches, dim=0)

            post_texts = [str(post.get("content") or "") for post in posts]
            post_embeddings = _encode_texts(post_texts, batch_size=32)

            for user in users:
                user_id = int(user["id"])
                context_bundle = user_context_index[user_id]
                user_context = str(context_bundle["context"])
                liked_posts = list(context_bundle["liked_posts"])
                cache_key = self._context_cache_key(
                    user_id,
                    user_context,
                    context_recent_posts=context_recent_posts,
                    include_like_trace=include_like_trace,
                    like_trace_window=like_trace_window,
                    include_like_trace_in_context=include_like_trace_in_context,
                )
                if cache_key not in embeddings_cache:
                    user_embedding = _encode_texts([user_context], batch_size=1)[0]
                    embeddings_cache[cache_key] = user_embedding
                else:
                    user_embedding = embeddings_cache[cache_key]

                sims = torch.matmul(post_embeddings, user_embedding)
                sims = self._blend_with_like_trace(
                    base_sims=sims,
                    liked_posts=liked_posts,
                    like_trace_weight=like_trace_weight,
                    include_like_trace=include_like_trace,
                    encode_texts=lambda texts, batch_size: _encode_texts(
                        texts,
                        batch_size=batch_size,
                    ),
                    score_profile=lambda like_profile: torch.matmul(
                        post_embeddings,
                        torch.nn.functional.normalize(like_profile, p=2, dim=0),
                    ),
                )

                rec_matrix[user_id] = self._top_post_ids_for_user(
                    user_id=user_id,
                    posts=posts,
                    scores=sims,
                    max_posts=max_posts,
                )

            return rec_matrix
        except Exception as e:
            logger.error("Error in TWHIN recsys: %s", e, exc_info=True)
            return {}

    def get_recommendations(
        self,
        username: str,
        limit: int = 10,
        recsys_type: str | None = None,
    ) -> list[dict]:
        """Get recommended posts for a user."""
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

                posts = self._parse_posts(cursor.fetchall())
                return posts
        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return []
