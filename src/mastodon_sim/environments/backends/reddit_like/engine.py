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
logger = logging.getLogger("RedditLikePlatform")


@dataclass
class Post:
    id: int
    user_id: int
    username: str
    subreddit_id: int
    subreddit_name: str
    title: str
    content: str
    created_at: float
    upvotes: int
    downvotes: int
    comment_count: int
    formatted_date: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "subreddit_id": self.subreddit_id,
            "subreddit_name": self.subreddit_name,
            "title": self.title,
            "content": self.content,
            "created_at": self.created_at,
            "upvotes": self.upvotes,
            "downvotes": self.downvotes,
            "score": self.upvotes - self.downvotes,
            "comment_count": self.comment_count,
            "formatted_date": self.formatted_date,
        }


@dataclass
class Comment:
    id: int
    post_id: int
    parent_id: int | None
    user_id: int
    username: str
    content: str
    created_at: float
    upvotes: int
    downvotes: int
    formatted_date: str = ""

    def to_dict(self):
        return {
            "id": self.id,
            "post_id": self.post_id,
            "parent_id": self.parent_id,
            "user_id": self.user_id,
            "username": self.username,
            "content": self.content,
            "created_at": self.created_at,
            "upvotes": self.upvotes,
            "downvotes": self.downvotes,
            "score": self.upvotes - self.downvotes,
            "formatted_date": self.formatted_date,
        }


class RedditLikePlatform:
    def __init__(self, db_path: str = "reddit_like.db", use_queue: bool = True):
        self.db_path = db_path
        self._init_db()
        self.use_queue = use_queue
        self._local = threading.local()

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
                    karma INTEGER DEFAULT 0
                )
            """)

            # Subreddits table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subreddits (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT UNIQUE NOT NULL,
                    description TEXT,
                    created_at REAL,
                    members_count INTEGER DEFAULT 0
                )
            """)

            # Subreddit Members
            conn.execute("""
                CREATE TABLE IF NOT EXISTS subreddit_members (
                    user_id INTEGER NOT NULL,
                    subreddit_id INTEGER NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (user_id, subreddit_id),
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(subreddit_id) REFERENCES subreddits(id)
                )
            """)

            # Posts table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subreddit_id INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT,
                    created_at REAL,
                    upvotes INTEGER DEFAULT 0,
                    downvotes INTEGER DEFAULT 0,
                    comment_count INTEGER DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(subreddit_id) REFERENCES subreddits(id)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_sub_created ON posts(subreddit_id, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_posts_user_created ON posts(user_id, created_at DESC)"
            )

            # Comments table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    post_id INTEGER NOT NULL,
                    parent_id INTEGER,
                    user_id INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL,
                    upvotes INTEGER DEFAULT 0,
                    downvotes INTEGER DEFAULT 0,
                    FOREIGN KEY(post_id) REFERENCES posts(id),
                    FOREIGN KEY(parent_id) REFERENCES comments(id),
                    FOREIGN KEY(user_id) REFERENCES users(id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id)")

            # Votes table (Handles both posts and comments)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS votes (
                    user_id INTEGER NOT NULL,
                    target_id INTEGER NOT NULL,
                    target_type TEXT NOT NULL, -- 'post' or 'comment'
                    vote_value INTEGER NOT NULL, -- 1 for upvote, -1 for downvote
                    created_at REAL,
                    PRIMARY KEY (user_id, target_id, target_type),
                    FOREIGN KEY(user_id) REFERENCES users(id)
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
                    action_type TEXT NOT NULL, -- 'upvote', 'reply', 'mention'
                    target_type TEXT NOT NULL, -- 'post' or 'comment'
                    reference_id INTEGER, -- The post or comment id
                    created_at REAL,
                    read BOOLEAN DEFAULT FALSE,
                    FOREIGN KEY(target_user_id) REFERENCES users(id),
                    FOREIGN KEY(source_user_id) REFERENCES users(id)
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
        """Yields a thread-local database connection (reused across calls)."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.db_path, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        try:
            yield conn
        except Exception:
            # On error, discard the connection so next call gets a fresh one.
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
            raise

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
                            main_sql, main_params = queries[0]
                            cursor = conn.execute(main_sql, main_params)
                            res = (
                                cursor.lastrowid
                                if cursor.lastrowid is not None
                                else cursor.rowcount
                            )
                            for sql, params in queries[1:]:
                                conn.execute(sql, params)
                            results.append((future, res, None))
                        except Exception as e:
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

    # --- Read / Helper Operations ---

    def get_user_id(self, username: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            return row["id"] if row else None

    def get_subreddit_id(self, name: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM subreddits WHERE name = ?", (name,)).fetchone()
            return row["id"] if row else None

    # --- Write Actions: Users & Subreddits ---

    def create_user(self, username: str, bio: str = "", sync: bool = True) -> Any:
        try:
            queries = [
                (
                    "INSERT INTO users (username, bio, created_at) VALUES (?, ?, ?)",
                    (username, bio, time.time()),
                )
            ]
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return self.get_user_id(username)

    def update_profile(self, username: str, bio: str, sync: bool = True) -> Any:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")
        queries = [("UPDATE users SET bio = ? WHERE id = ?", (bio, user_id))]
        return self._execute_write(queries, sync=sync)

    def create_subreddit(self, name: str, description: str = "", sync: bool = True) -> Any:
        # Strip r/ if user provided it
        name = name.removeprefix("r/")
        try:
            queries = [
                (
                    "INSERT INTO subreddits (name, description, created_at) VALUES (?, ?, ?)",
                    (name, description, time.time()),
                )
            ]
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return self.get_subreddit_id(name)

    def join_subreddit(self, username: str, subreddit_name: str, sync: bool = True):
        user_id = self.get_user_id(username)
        sub_id = self.get_subreddit_id(subreddit_name)
        if not user_id or not sub_id:
            raise ValueError("User or Subreddit not found")

        queries = [
            (
                "INSERT INTO subreddit_members (user_id, subreddit_id, created_at) VALUES (?, ?, ?)",
                (user_id, sub_id, time.time()),
            ),
            ("UPDATE subreddits SET members_count = members_count + 1 WHERE id = ?", (sub_id,)),
        ]
        try:
            return self._execute_write(queries, sync=sync)
        except sqlite3.IntegrityError:
            return False  # Already joined

    def leave_subreddit(self, username: str, subreddit_name: str, sync: bool = True):
        user_id = self.get_user_id(username)
        sub_id = self.get_subreddit_id(subreddit_name)
        if not user_id or not sub_id:
            raise ValueError("User or Subreddit not found")

        queries = [
            (
                "DELETE FROM subreddit_members WHERE user_id = ? AND subreddit_id = ?",
                (user_id, sub_id),
            ),
            ("UPDATE subreddits SET members_count = members_count - 1 WHERE id = ?", (sub_id,)),
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
            )
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

    # --- Write Actions: Content & Votes ---

    def create_post(
        self, username: str, subreddit_name: str, title: str, content: str = "", sync: bool = True
    ) -> Any:
        user_id = self.get_user_id(username)
        subreddit_name = subreddit_name.removeprefix("r/")
        sub_id = self.get_subreddit_id(subreddit_name)
        if not user_id or not sub_id:
            raise ValueError("User or Subreddit not found")

        queries = [
            (
                "INSERT INTO posts (user_id, subreddit_id, title, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, sub_id, title, content, time.time()),
            )
        ]
        return self._execute_write(queries, sync=sync)

    def create_comment(
        self,
        username: str,
        post_id: int,
        content: str,
        parent_id: int | None = None,
        sync: bool = True,
    ) -> Any:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError(f"User {username} not found")

        with self.get_connection() as conn:
            post = conn.execute("SELECT user_id FROM posts WHERE id = ?", (post_id,)).fetchone()
            if not post:
                raise ValueError("Post not found")
            target_user_id = post["user_id"]

            if parent_id:
                parent = conn.execute(
                    "SELECT user_id FROM comments WHERE id = ?", (parent_id,)
                ).fetchone()
                if not parent:
                    raise ValueError("Parent comment not found")
                target_user_id = parent["user_id"]

        queries = [
            (
                "INSERT INTO comments (post_id, parent_id, user_id, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (post_id, parent_id, user_id, content, time.time()),
            ),
            ("UPDATE posts SET comment_count = comment_count + 1 WHERE id = ?", (post_id,)),
        ]

        # Activity/Notification for reply
        if target_user_id != user_id:
            queries.append(
                (
                    "INSERT INTO activities (target_user_id, source_user_id, action_type, target_type, reference_id, created_at) VALUES (?, ?, 'reply', ?, ?, ?)",
                    (
                        target_user_id,
                        user_id,
                        "comment" if parent_id else "post",
                        parent_id or post_id,
                        time.time(),
                    ),
                )
            )

        return self._execute_write(queries, sync=sync)

    def vote(
        self, username: str, target_id: int, target_type: str, vote_value: int, sync: bool = True
    ):
        """Vote value is 1 for upvote, -1 for downvote, 0 to revoke vote."""
        if target_type not in ("post", "comment"):
            raise ValueError("target_type must be post or comment")
        if vote_value not in (-1, 0, 1):
            raise ValueError("vote_value must be -1, 0, or 1")

        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")

        table = f"{target_type}s"

        with self.get_connection() as conn:
            target = conn.execute(
                f"SELECT user_id FROM {table} WHERE id = ?", (target_id,)
            ).fetchone()
            if not target:
                raise ValueError(f"{target_type.capitalize()} not found")
            owner_id = target["user_id"]

            # Get existing vote
            existing = conn.execute(
                "SELECT vote_value FROM votes WHERE user_id = ? AND target_id = ? AND target_type = ?",
                (user_id, target_id, target_type),
            ).fetchone()
            existing_val = existing["vote_value"] if existing else 0

        if existing_val == vote_value:
            return True  # Nothing to do

        queries: list[tuple[str, tuple[Any, ...]]] = []
        # Update or delete vote record
        if vote_value == 0:
            queries.append(
                (
                    "DELETE FROM votes WHERE user_id = ? AND target_id = ? AND target_type = ?",
                    (user_id, target_id, target_type),
                )
            )
        elif existing_val == 0:
            queries.append(
                (
                    "INSERT INTO votes (user_id, target_id, target_type, vote_value, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, target_id, target_type, vote_value, time.time()),
                )
            )
        else:
            queries.append(
                (
                    "UPDATE votes SET vote_value = ? WHERE user_id = ? AND target_id = ? AND target_type = ?",
                    (vote_value, user_id, target_id, target_type),
                )
            )

        # Calculate deltas for counts
        # e.g. existing was 1, new is -1. old upvote -1, new downvote +1
        up_delta = 0
        down_delta = 0

        if existing_val == 1:
            up_delta -= 1
        elif existing_val == -1:
            down_delta -= 1

        if vote_value == 1:
            up_delta += 1
        elif vote_value == -1:
            down_delta += 1

        # Apply deltas to target table
        if up_delta != 0 or down_delta != 0:
            queries.append(
                (
                    f"UPDATE {table} SET upvotes = upvotes + (?), downvotes = downvotes + (?) WHERE id = ?",
                    (up_delta, down_delta, target_id),
                )
            )

        # Apply karma delta to user (karma = simple sum of up_delta - down_delta)
        karma_delta = up_delta - down_delta
        if karma_delta != 0:
            queries.append(
                ("UPDATE users SET karma = karma + (?) WHERE id = ?", (karma_delta, owner_id))
            )

        # Submit to queue
        return self._execute_write(queries, sync=sync)

    # --- Read / Feed Methods ---

    def view_profile(self, username: str) -> dict[str, Any] | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return dict(row)
            return None

    def search_subreddits(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        with self.get_connection() as conn:
            search_term = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM subreddits WHERE name LIKE ? OR description LIKE ? LIMIT ?",
                (search_term, search_term, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def _parse_posts(self, rows) -> list[dict]:
        posts = []
        for row in rows:
            post = Post(
                id=row["id"],
                user_id=row["user_id"],
                username=row["username"],
                subreddit_id=row["subreddit_id"],
                subreddit_name=row["subreddit_name"],
                title=row["title"],
                content=row["content"],
                created_at=row["created_at"],
                upvotes=row["upvotes"],
                downvotes=row["downvotes"],
                comment_count=row["comment_count"],
                formatted_date=time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])
                ),
            )
            posts.append(post.to_dict())
        return posts

    def get_feed(
        self,
        feed_type: str,
        username: str | None = None,
        limit: int = 25,
        cursor: int | None = None,
    ) -> dict[str, Any]:
        """Generic feed router. Supported types: 'home', 'popular'"""
        if feed_type == "home":
            assert username is not None, "username required for home feed"
            return self._feed_home(username, limit, cursor)
        if feed_type == "popular":
            return self._feed_popular(limit, cursor)
        raise ValueError(f"Unknown feed_type: {feed_type}")

    def _feed_home(
        self, username: str, limit: int = 25, cursor: int | None = None
    ) -> dict[str, Any]:
        """Posts from subreddits the user has joined, ordered by creation."""
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")

        query = """
            SELECT p.*, u.username, s.name as subreddit_name
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN subreddits s ON p.subreddit_id = s.id
            WHERE p.subreddit_id IN (SELECT subreddit_id FROM subreddit_members WHERE user_id = ?)
            AND (? IS NULL OR p.id < ?)
            AND p.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (user_id, cursor, cursor, user_id, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def get_subreddit_feed(
        self, subreddit_name: str, limit: int = 25, cursor: int | None = None
    ) -> dict[str, Any]:
        subreddit_name = subreddit_name.removeprefix("r/")
        sub_id = self.get_subreddit_id(subreddit_name)
        if not sub_id:
            raise ValueError("Subreddit not found")

        # Sort by hot approximation (upvotes * simple time decay, or just recent + score for now)
        # We will just do recent for simplicity and speed.
        query = """
            SELECT p.*, u.username, s.name as subreddit_name
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN subreddits s ON p.subreddit_id = s.id
            WHERE p.subreddit_id = ?
            AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (sub_id, cursor, cursor, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def _feed_popular(self, limit: int = 25, cursor: int | None = None) -> dict[str, Any]:
        # Score-based sorting (Upvotes - Downvotes). Pagination with score is harder than ID, so we'll
        # approximate "Trending" by grabbing high score posts from recent IDs.
        query = """
            SELECT p.*, u.username, s.name as subreddit_name
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN subreddits s ON p.subreddit_id = s.id
            WHERE (? IS NULL OR p.id < ?)
            AND (p.upvotes - p.downvotes) > 0
            ORDER BY (p.upvotes - p.downvotes) DESC, p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (cursor, cursor, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def get_user_feed(
        self, username: str, limit: int = 25, cursor: int | None = None
    ) -> dict[str, Any]:
        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")
        query = """
            SELECT p.*, u.username, s.name as subreddit_name
            FROM posts p
            JOIN users u ON p.user_id = u.id
            JOIN subreddits s ON p.subreddit_id = s.id
            WHERE p.user_id = ?
            AND (? IS NULL OR p.id < ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (user_id, cursor, cursor, limit)).fetchall()
            posts = self._parse_posts(rows)
            return {"posts": posts, "next_cursor": posts[-1]["id"] if posts else None}

    def get_post_comments(
        self, post_id: int, limit: int = 100, as_tree: bool = True
    ) -> list[dict[str, Any]]:
        """Fetch comments for a post. If as_tree is True, returns nested comments (Reddit style),
        otherwise returns a flat list.
        """
        query = """
            SELECT c.*, u.username
            FROM comments c
            JOIN users u ON c.user_id = u.id
            WHERE c.post_id = ?
            ORDER BY (c.upvotes - c.downvotes) DESC, c.id ASC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(query, (post_id, limit)).fetchall()

            flat_comments = {}
            top_level = []

            for row in rows:
                c = Comment(
                    id=row["id"],
                    post_id=row["post_id"],
                    parent_id=row["parent_id"],
                    user_id=row["user_id"],
                    username=row["username"],
                    content=row["content"],
                    created_at=row["created_at"],
                    upvotes=row["upvotes"],
                    downvotes=row["downvotes"],
                    formatted_date=time.strftime(
                        "%Y-%m-%d %H:%M:%S", time.localtime(row["created_at"])
                    ),
                )
                cdict = c.to_dict()
                if as_tree:
                    cdict["replies"] = []  # Prepare nesting
                    flat_comments[cdict["id"]] = cdict
                else:
                    top_level.append(cdict)

            if not as_tree:
                return top_level

            # Build Tree
            for c_id, cdict in flat_comments.items():
                if cdict.get("parent_id"):
                    parent = flat_comments.get(cdict["parent_id"])
                    if parent:
                        parent["replies"].append(cdict)
                    else:
                        top_level.append(cdict)  # Parent wasn't fetched, treat as top level
                else:
                    top_level.append(cdict)

            return top_level

    # --- DMs and Activities ---

    def send_dm(self, username: str, target_username: str, content: str, sync: bool = True) -> Any:
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
            return [dict(r) for r in rows]
