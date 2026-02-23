from __future__ import annotations

import concurrent.futures
import logging
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
