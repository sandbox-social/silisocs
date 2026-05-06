"""engine module. Auto-generated module docstring.
"""

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
    """Post.
    """

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
        """to_dict.
        """

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
    """Comment.
    """

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
        """to_dict.
        """

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
    """RedditLikePlatform.
    
    Constructor parameters:
    
    __init__.
    
    :param str db_path:
    :type db_path: str
    :param bool use_queue:
    :type use_queue: bool
    """

    SUPPORTED_RECSYS_TYPES = frozenset({"reddit", "twhin"})

    def __init__(self, db_path: str = "reddit_like.db", use_queue: bool = True):
        """__init__.
    
        :param str db_path:
        :type db_path: str
        :param bool use_queue:
        :type use_queue: bool
        """

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
                    dislikes_count INTEGER DEFAULT 0,
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
        """get_user_id.
    
        :param str username:
        :type username: str
    
        :returns: int | None
        :rtype: int | None
        """

        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            return row["id"] if row else None

    def get_subreddit_id(self, name: str) -> int | None:
        """get_subreddit_id.
    
        :param str name:
        :type name: str
    
        :returns: int | None
        :rtype: int | None
        """

        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM subreddits WHERE name = ?", (name,)).fetchone()
            return row["id"] if row else None

    # --- Write Actions: Users & Subreddits ---

    def create_user(self, username: str, bio: str = "", sync: bool = True) -> Any:
        """create_user.
    
        :param str username:
        :type username: str
        :param str bio:
        :type bio: str
        :param bool sync:
        :type sync: bool
    
        :returns: Any
        :rtype: Any
        """

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
        """update_profile.
    
        :param str username:
        :type username: str
        :param str bio:
        :type bio: str
        :param bool sync:
        :type sync: bool
    
        :returns: Any
        :rtype: Any
        """

        user_id = self.get_user_id(username)
        if not user_id:
            raise ValueError("User not found")
        queries = [("UPDATE users SET bio = ? WHERE id = ?", (bio, user_id))]
        return self._execute_write(queries, sync=sync)

    def create_subreddit(self, name: str, description: str = "", sync: bool = True) -> Any:
        # Strip r/ if user provided it
        """create_subreddit.

        :param str name:
        :type name: str
        :param str description:
        :type description: str
        :param bool sync:
        :type sync: bool

        :returns: Any
        :rtype: Any
        """

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
        """join_subreddit.
    
        :param str username:
        :type username: str
        :param str subreddit_name:
        :type subreddit_name: str
        :param bool sync:
        :type sync: bool
        """

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
        """leave_subreddit.
    
        :param str username:
        :type username: str
        :param str subreddit_name:
        :type subreddit_name: str
        :param bool sync:
        :type sync: bool
        """

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
        """block.
    
        :param str username:
        :type username: str
        :param str target_username:
        :type target_username: str
        :param bool sync:
        :type sync: bool
        """

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
        """unblock.
    
        :param str username:
        :type username: str
        :param str target_username:
        :type target_username: str
        :param bool sync:
        :type sync: bool
        """

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
        """create_post.

        :param str username:
        :type username: str
        :param str subreddit_name:
        :type subreddit_name: str
        :param str title:
        :type title: str
        :param str content:
        :type content: str
        :param bool sync:
        :type sync: bool

        :returns: Any
        :rtype: Any
        """

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
        """create_comment.

        :param str username:
        :type username: str
        :param int post_id:
        :type post_id: int
        :param str content:
        :type content: str
        :param int | None parent_id:
        :type parent_id: int | None
        :param bool sync:
        :type sync: bool

        :returns: Any
        :rtype: Any
        """

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
        """view_profile.
    
        :param str username:
        :type username: str
    
        :returns: dict[str, Any] | None
        :rtype: dict[str, Any] | None
        """

        with self.get_connection() as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if row:
                return dict(row)
            return None

    def search_subreddits(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """search_subreddits.
    
        :param str query:
        :type query: str
        :param int limit:
        :type limit: int
    
        :returns: list[dict[str, Any]]
        :rtype: list[dict[str, Any]]
        """

        with self.get_connection() as conn:
            search_term = f"%{query}%"
            rows = conn.execute(
                "SELECT * FROM subreddits WHERE name LIKE ? OR description LIKE ? LIMIT ?",
                (search_term, search_term, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def _parse_posts(self, rows) -> list[dict]:
        """_parse_posts.
    
        :param rows:
    
        :returns: list[dict]
        :rtype: list[dict]
        """

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
        """get_subreddit_feed.

        :param str subreddit_name:
        :type subreddit_name: str
        :param int limit:
        :type limit: int
        :param int | None cursor:
        :type cursor: int | None

        :returns: dict[str, Any]
        :rtype: dict[str, Any]
        """

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
        """_feed_popular.

        :param int limit:
        :type limit: int
        :param int | None cursor:
        :type cursor: int | None

        :returns: dict[str, Any]
        :rtype: dict[str, Any]
        """

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
        """get_user_feed.

        :param str username:
        :type username: str
        :param int limit:
        :type limit: int
        :param int | None cursor:
        :type cursor: int | None

        :returns: dict[str, Any]
        :rtype: dict[str, Any]
        """

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
        """send_dm.
    
        :param str username:
        :type username: str
        :param str target_username:
        :type target_username: str
        :param str content:
        :type content: str
        :param bool sync:
        :type sync: bool
    
        :returns: Any
        :rtype: Any
        """

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
        """view_dms_with.

        :param str username:
        :type username: str
        :param str target_username:
        :type target_username: str
        :param int limit:
        :type limit: int

        :returns: list[dict[str, Any]]
        :rtype: list[dict[str, Any]]
        """

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

    # ================================================================ #
    # Timeline Selection Strategies (mirrored from Twitter)
    # ================================================================ #

    TIMELINE_STRATEGIES = {
        "follower_chronological": {
            "description": "Home feed from subscribed subreddits (chronological)",
            "internal_feed": "home",
        },
        "pure_recsys": {
            "description": "Pure recommendation algorithm feed",
            "internal_feed": None,  # Uses get_recommendations()
        },
        "hybrid_recsys_follower": {
            "description": "Hybrid: recommendations + home feed (configurable split)",
            "internal_feed": None,  # Uses both
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
            **timeline_config: Strategy-specific config (e.g., recsys_ratio, follower_ratio)

        Returns
        -------
            List of posts ordered by strategy
        """
        if strategy == "follower_chronological":
            feed = self.get_feed("home", username, limit)
            return feed.get("posts", []) if feed else []

        if strategy == "pure_recsys":
            # Pure recommendations only
            return self.get_recommendations(username, limit, recsys_type=recsys_type)

        if strategy == "hybrid_recsys_follower":
            # Blend recommendations and home feed with configurable ratio
            recsys_ratio = timeline_config.get("recsys_ratio", 0.6)
            follower_ratio = timeline_config.get("follower_ratio", 0.4)

            rec_count = max(1, int(limit * recsys_ratio))
            home_count = max(1, int(limit * follower_ratio))

            rec_posts = self.get_recommendations(
                username,
                rec_count,
                recsys_type=recsys_type,
            )
            home_feed = self.get_feed("home", username, home_count)
            home_posts = home_feed.get("posts", []) if home_feed else []

            # Merge and deduplicate
            seen_ids = set()
            combined = []

            # Add recsys first (higher priority)
            for post in rec_posts or []:
                post_id = post.get("id", post.get("post_id"))
                if post_id not in seen_ids:
                    combined.append(post)
                    seen_ids.add(post_id)

            # Then add home feed posts
            for post in home_posts:
                post_id = post.get("id", post.get("post_id"))
                if post_id not in seen_ids:
                    combined.append(post)
                    seen_ids.add(post_id)

            return combined[:limit]

        # Fallback: use follower_chronological
        logger.warning(
            f"Unknown timeline strategy '{strategy}', falling back to follower_chronological"
        )
        return self.get_timeline("follower_chronological", username, limit, **timeline_config)

    # ================================================================ #
    # OASIS-Compatible Extended Methods (mirrored from Twitter)
    # ================================================================ #

    def dislike_post(self, username: str, post_id: int) -> bool:
        """Add a downvote to a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if already downvoted
                cursor = conn.execute(
                    "SELECT 1 FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if cursor.fetchone():
                    return False

                # Add downvote
                now = time.time()
                conn.execute(
                    "INSERT INTO dislikes (user_id, post_id, created_at) VALUES (?, ?, ?)",
                    (user_id, post_id, now),
                )
                conn.execute(
                    "UPDATE posts SET downvotes = downvotes + 1 WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error downvoting post {post_id}: {e}")
            return False

    def unlike_post(self, username: str, post_id: int) -> bool:
        """Remove an upvote from a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if upvote exists
                cursor = conn.execute(
                    "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if not cursor.fetchone():
                    return False

                # Remove upvote
                conn.execute(
                    "DELETE FROM likes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                conn.execute(
                    "UPDATE posts SET upvotes = MAX(0, upvotes - 1) WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error removing upvote for post {post_id}: {e}")
            return False

    def undo_dislike_post(self, username: str, post_id: int) -> bool:
        """Remove a downvote from a post."""
        try:
            with self.get_connection() as conn:
                user_id = self.get_user_id(username)
                if not user_id:
                    return False

                # Check if downvote exists
                cursor = conn.execute(
                    "SELECT 1 FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                if not cursor.fetchone():
                    return False

                # Remove downvote
                conn.execute(
                    "DELETE FROM dislikes WHERE user_id = ? AND post_id = ?",
                    (user_id, post_id),
                )
                conn.execute(
                    "UPDATE posts SET downvotes = MAX(0, downvotes - 1) WHERE id = ?",
                    (post_id,),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Error removing downvote for post {post_id}: {e}")
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
                           (p.upvotes + p.comment_count) as engagement
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
                            "title": row[3],
                            "content": row[4],
                            "created_at": row[5],
                            "upvotes": row[6],
                            "downvotes": row[7],
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

    def init_recsys(self, recsys_type: str = "reddit") -> None:
        """Initialize recommendation system for a specific algorithm type.

        Args:
            recsys_type: Algorithm type ("reddit" or "twhin")
                        Can be called multiple times to enable multiple algorithms simultaneously.
        """
        recsys_type = str(recsys_type or "").strip().lower()
        if recsys_type not in self.SUPPORTED_RECSYS_TYPES:
            raise ValueError(
                "Unsupported recsys_type for Reddit-like backend: "
                f"'{recsys_type}'. Supported: {sorted(self.SUPPORTED_RECSYS_TYPES)}"
            )

        # Initialize dict on first call
        if not hasattr(self, "_recsys_types"):
            self._recsys_types: dict[str, dict] = {}

        # Add this algorithm type to the set (cumulative, not overwriting)
        self._recsys_types[recsys_type] = {
            "type": recsys_type,
            "embeddings_cache": {},
            "model": None,
        }

        # Load embedding model if needed
        if recsys_type == "twhin":
            try:
                from sentence_transformers import SentenceTransformer

                model_name = "sentence-transformers/Twitter-roBERTa-base"
                model = SentenceTransformer(model_name)
                self._recsys_types[recsys_type]["model"] = model
                logger.info(f"Loaded {recsys_type} recsys model")
            except ImportError:
                logger.warning(
                    f"sentence-transformers not available for {recsys_type}, embeddings disabled for this type"
                )
                self._recsys_types[recsys_type]["model"] = None

        logger.info(
            f"Initialized recsys type '{recsys_type}'. Active types: {list(self._recsys_types.keys())}"
        )

    def reddit_hot_score(self, upvotes: int, downvotes: int, created_at: float) -> float:
        """Reddit hot-score algorithm."""
        import math

        score = upvotes - downvotes
        now = time.time()
        age_seconds = now - created_at

        order = math.log10(max(abs(score), 1))
        sign = 1 if score > 0 else -1 if score < 0 else 0

        return sign * order + (age_seconds / 45000)

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

                users = [
                    {"id": r[0], "username": r[1], "bio": r[2] or ""} for r in cursor.fetchall()
                ]

                # Get recent posts
                cursor = conn.execute(
                    "SELECT id, user_id, content, created_at, upvotes, downvotes FROM posts ORDER BY created_at DESC LIMIT 1000"
                )
                posts = [
                    {
                        "id": r[0],
                        "user_id": r[1],
                        "content": r[2],
                        "created_at": r[3],
                        "upvotes": r[4],
                        "downvotes": r[5],
                    }
                    for r in cursor.fetchall()
                ]

                if not users or not posts:
                    logger.debug("No users or posts found; skipping recommendations update")
                    return

                # Clear old recommendations (once per update)
                conn.execute("DELETE FROM recommendations")

                # Generate recommendations for each algorithm type
                for recsys_type, state in self._recsys_types.items():
                    logger.debug(f"Computing {recsys_type} recommendations for {len(users)} users")

                    if recsys_type == "reddit":
                        rec_matrix = self._rec_reddit(users, posts, max_posts)
                    elif recsys_type == "twhin":
                        # Pass the model state for this algorithm type
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

    def _rec_reddit(self, users: list, posts: list, max_posts: int) -> dict:
        """Reddit hot-score recommendations."""
        rec_matrix = {}
        for user in users:
            user_id = user["id"]
            scored = []
            for post in posts:
                if post["user_id"] != user_id:
                    score = self.reddit_hot_score(
                        post["upvotes"], post["downvotes"], post["created_at"]
                    )
                    scored.append((post["id"], score))

            scored.sort(key=lambda x: x[1], reverse=True)
            rec_matrix[user_id] = [p[0] for p in scored[:max_posts]]

        return rec_matrix

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

        if model is None:
            return {}

        try:
            rec_matrix = {}

            # Encode posts (batch)
            post_texts = [p["content"] for p in posts]
            post_embeddings = model.encode(post_texts, batch_size=32, convert_to_tensor=True)

            # For each user
            for user in users:
                user_id = user["id"]
                bio = user["bio"] or "no bio"

                # Get or compute user embedding
                cache_key = ("user", user_id)
                if cache_key not in embeddings_cache:
                    user_emb = model.encode(bio, convert_to_tensor=True)
                    embeddings_cache[cache_key] = user_emb
                else:
                    user_emb = embeddings_cache[cache_key]

                # Compute similarities
                import torch

                sims = torch.nn.functional.cosine_similarity(user_emb.unsqueeze(0), post_embeddings)

                # Score posts
                scored = []
                for i, post in enumerate(posts):
                    if post["user_id"] != user_id:
                        scored.append((post["id"], float(sims[i])))

                scored.sort(key=lambda x: x[1], reverse=True)
                rec_matrix[user_id] = [p[0] for p in scored[:max_posts]]

            return rec_matrix
        except Exception as e:
            logger.error(f"Error in embedding recsys: {e}", exc_info=True)
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

                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Error getting recommendations: {e}")
            return []
