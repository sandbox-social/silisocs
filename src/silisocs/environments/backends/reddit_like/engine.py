from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.social.sqlite_engine import SqliteSocialEngineBase

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


class RedditLikePlatform(SqliteSocialEngineBase):
    SUPPORTED_RECSYS_TYPES = frozenset({"reddit", "twhin"})
    default_db_path = "reddit_like.db"

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
            # Plain created_at index backs the recsys candidate query
            # (ORDER BY created_at DESC LIMIT 1000), which the composite
            # (subreddit_id|user_id, created_at) indexes cannot serve.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at DESC)")

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

            # Mutes table (one-directional: muter hides mutee from their feed)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mutes (
                    muter_id INTEGER NOT NULL,
                    mutee_id INTEGER NOT NULL,
                    created_at REAL,
                    PRIMARY KEY (muter_id, mutee_id),
                    FOREIGN KEY(muter_id) REFERENCES users(id),
                    FOREIGN KEY(mutee_id) REFERENCES users(id)
                )
            """)

            # Reports table (moderation reports; multiple allowed per user/post)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS reports (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    post_id INTEGER NOT NULL,
                    reason TEXT,
                    created_at REAL,
                    FOREIGN KEY(user_id) REFERENCES users(id),
                    FOREIGN KEY(post_id) REFERENCES posts(id)
                )
            """)

            # Initialize recommendation schema tables
            self._init_recommendation_schema(conn)

    # --- Read / Helper Operations ---

    def get_subreddit_id(self, name: str) -> int | None:
        with self.get_connection() as conn:
            row = conn.execute("SELECT id FROM subreddits WHERE name = ?", (name,)).fetchone()
            return row["id"] if row else None

    # --- Write Actions: Users & Subreddits ---

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

        # Only decrement when the membership actually exists, and clamp at zero, so
        # leaving a subreddit the user never joined cannot corrupt members_count.
        # Mirrors the duplicate-join guard in join_subreddit.
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM subreddit_members WHERE user_id = ? AND subreddit_id = ?",
                (user_id, sub_id),
            ).fetchone()
        if not existing:
            return False

        queries = [
            (
                "DELETE FROM subreddit_members WHERE user_id = ? AND subreddit_id = ?",
                (user_id, sub_id),
            ),
            (
                "UPDATE subreddits SET members_count = MAX(0, members_count - 1) WHERE id = ?",
                (sub_id,),
            ),
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
            AND p.user_id NOT IN (SELECT mutee_id FROM mutes WHERE muter_id = ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        with self.get_connection() as conn:
            rows = conn.execute(
                query, (user_id, cursor, cursor, user_id, user_id, limit)
            ).fetchall()
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

    # get_timeline (follower_chronological / pure_recsys / hybrid_recsys_follower) is
    # inherited from SqliteSocialEngineBase; Reddit uses the default "home" follower
    # feed, so no override is needed.

    # ================================================================ #
    # Extended social methods (mirrored from Twitter)
    # ================================================================ #

    def _current_post_vote(self, user_id: int, post_id: int) -> int:
        """Return the user's current vote on a post (1, -1, or 0 if none)."""
        with self.get_connection() as conn:
            row = conn.execute(
                "SELECT vote_value FROM votes "
                "WHERE user_id = ? AND target_id = ? AND target_type = 'post'",
                (user_id, post_id),
            ).fetchone()
        return int(row["vote_value"]) if row else 0

    def dislike_post(self, username: str, post_id: int) -> bool:
        """Downvote a post.

        Routed through the canonical ``vote()`` (votes table) so a downvote shares
        one source of truth with the ``downvote`` action and cannot double-count
        ``posts.downvotes``. Returns False if the user has already downvoted.
        """
        user_id = self.get_user_id(username)
        if not user_id:
            return False
        if self._current_post_vote(user_id, post_id) == -1:
            return False
        try:
            self.vote(username, post_id, "post", -1)
            return True
        except ValueError as e:
            logger.error(f"Error downvoting post {post_id}: {e}")
            return False

    def unlike_post(self, username: str, post_id: int) -> bool:
        """Remove an upvote from a post (revokes the user's upvote in ``votes``)."""
        user_id = self.get_user_id(username)
        if not user_id:
            return False
        if self._current_post_vote(user_id, post_id) != 1:
            return False
        try:
            self.vote(username, post_id, "post", 0)
            return True
        except ValueError as e:
            logger.error(f"Error removing upvote for post {post_id}: {e}")
            return False

    def undo_dislike_post(self, username: str, post_id: int) -> bool:
        """Remove a downvote from a post (revokes the user's downvote in ``votes``)."""
        user_id = self.get_user_id(username)
        if not user_id:
            return False
        if self._current_post_vote(user_id, post_id) != -1:
            return False
        try:
            self.vote(username, post_id, "post", 0)
            return True
        except ValueError as e:
            logger.error(f"Error removing downvote for post {post_id}: {e}")
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
                    # Named access (sqlite3.Row) so a schema column reorder can't
                    # silently corrupt the projection.
                    posts.append(
                        {
                            "id": row["id"],
                            "user_id": row["user_id"],
                            "title": row["title"],
                            "content": row["content"],
                            "created_at": row["created_at"],
                            "upvotes": row["upvotes"],
                            "downvotes": row["downvotes"],
                            "username": row["username"],
                            "engagement_score": row["engagement"],
                        }
                    )
                return posts
        except Exception as e:
            logger.error(f"Error getting trending posts: {e}")
            return []

    # ================================================================ #
    # Recommendation system
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
            logger.warning(
                "update_recommendations called with no live recsys types; recommendations "
                "will not refresh. After a checkpoint restore the update component should "
                "re-initialize recsys (via recsys_active_types reconciliation)."
            )
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
            recsys_state: State dict for this algorithm type containing model
                and embeddings_cache.
        """
        if recsys_state is None:
            raise ValueError("Embedding recommendations require initialized recsys_state.")

        model = recsys_state.get("model")
        embeddings_cache = recsys_state.get("embeddings_cache", {})

        if model is None:
            return {}

        try:
            import torch

            rec_matrix = {}

            # Posts are append-only, so batch-encode only ids not already in the
            # persistent embeddings_cache and reassemble in post order, avoiding
            # re-encoding the same posts each recsys update (mirrors the Twitter backend).
            post_ids = [int(p["id"]) for p in posts]
            missing = [
                (i, p["content"])
                for i, (pid, p) in enumerate(zip(post_ids, posts, strict=False))
                if ("post_emb", pid) not in embeddings_cache
            ]
            if missing:
                new_embeddings = model.encode(
                    [text for _, text in missing], batch_size=32, convert_to_tensor=True
                )
                for row, (idx, _) in enumerate(missing):
                    embeddings_cache[("post_emb", post_ids[idx])] = new_embeddings[row]
            post_embeddings = torch.stack([embeddings_cache[("post_emb", pid)] for pid in post_ids])

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
