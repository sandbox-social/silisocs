from __future__ import annotations

import logging
import os
import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from silisocs.environments.backends.social.sqlite_engine import SqliteSocialEngineBase

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


class TwitterLikePlatform(SqliteSocialEngineBase):
    SUPPORTED_RECSYS_TYPES = frozenset({"twitter", "twitter_tfidf", "twhin"})
    default_db_path = "twitter_like.db"
    # Trending hooks read by SqliteSocialEngineBase.get_trending_posts.
    _trending_engagement_sql = "(p.likes_count + p.reposts_count)"
    _trending_post_fields = ("content", "likes_count", "reposts_count")

    def _init_platform_schema(self, conn: sqlite3.Connection) -> None:
        """Create the Twitter-like tables (the shared ones live on the base)."""
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
        # Home-feed fan-out reads are `user_id IN (...) ORDER BY id DESC LIMIT k`;
        # this composite serves both the membership filter and the id ordering.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_user_id_desc ON posts(user_id, id DESC)")
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
        # Reverse lookup ("who liked post X"); the PK only covers user-first.
        conn.execute("CREATE INDEX IF NOT EXISTS idx_likes_post ON likes(post_id)")

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

        # Dislikes table (negative reactions; mirrors likes)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS dislikes (
                user_id INTEGER NOT NULL,
                post_id INTEGER NOT NULL,
                created_at REAL,
                PRIMARY KEY (user_id, post_id),
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(post_id) REFERENCES posts(id)
            )
        """)

    # --- User Management ---

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
                raise ValueError(f"Post {post_id} not found")

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
                raise ValueError(f"Post {post_id} not found")

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
                raise ValueError(f"Post {post_id} not found")

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

        # Only decrement when a like actually exists, and clamp at zero, so an
        # unlike on a never-liked post (or a double-unlike) cannot drive
        # likes_count negative. Mirrors unlike_post().
        with self.get_connection() as conn:
            existing = conn.execute(
                "SELECT 1 FROM likes WHERE user_id = ? AND post_id = ?",
                (user_id, post_id),
            ).fetchone()
        if not existing:
            return False

        queries = [
            ("DELETE FROM likes WHERE user_id = ? AND post_id = ?", (user_id, post_id)),
            ("UPDATE posts SET likes_count = MAX(0, likes_count - 1) WHERE id = ?", (post_id,)),
        ]
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

    def add_follows(self, edges: Sequence[tuple[str, str]]) -> list[tuple[str, str]]:
        """Bulk-apply follow edges in ONE queued transaction (init fast path).

        Dedupes and drops self-follows / unknown users / edges already in the
        DB (a pre-populated DB must not get duplicate 'follow' activities or
        re-report existing edges as applied), inserts the new edges with
        ``OR IGNORE``, records one 'follow' activity per applied edge, then
        recomputes follower/following counters authoritatively from the follows
        table (immune to duplicate-edge drift). Returns the applied
        ``(follower, followee)`` username pairs. At setup scale this replaces
        4·E serialized statements across E writer round-trips with one.
        """
        now = time.time()
        with self.get_connection() as conn:
            existing: set[tuple[int, int]] = {
                (int(r[0]), int(r[1]))
                for r in conn.execute("SELECT follower_id, followee_id FROM follows").fetchall()
            }
        applied: list[tuple[str, str]] = []
        seen: set[tuple[int, int]] = set(existing)
        queries: list[tuple[str, tuple[Any, ...]]] = []
        for follower, followee in edges:
            follower_id = self.get_user_id(follower)
            followee_id = self.get_user_id(followee)
            if not follower_id or not followee_id or follower_id == followee_id:
                continue
            pair = (follower_id, followee_id)
            if pair in seen:
                continue
            seen.add(pair)
            applied.append((follower, followee))
            queries.append(
                (
                    "INSERT OR IGNORE INTO follows (follower_id, followee_id, created_at) VALUES (?, ?, ?)",
                    (follower_id, followee_id, now),
                )
            )
            queries.append(
                (
                    "INSERT INTO activities (target_user_id, source_user_id, action_type, created_at) VALUES (?, ?, 'follow', ?)",
                    (followee_id, follower_id, now),
                )
            )
        if not queries:
            return []
        queries.append(
            (
                "UPDATE users SET "
                "following_count = (SELECT COUNT(*) FROM follows WHERE follower_id = users.id), "
                "followers_count = (SELECT COUNT(*) FROM follows WHERE followee_id = users.id)",
                (),
            )
        )
        self._execute_write(queries, sync=True)
        return applied

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
            AND p.user_id NOT IN (SELECT mutee_id FROM mutes WHERE muter_id = ?)
            ORDER BY p.id DESC
            LIMIT ?
        """
        return self._paged_feed(query, (user_id, cursor, cursor, user_id, user_id, limit))

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
        return self._paged_feed(query, (target_id, cursor, cursor, limit))

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
            AND p.user_id NOT IN (SELECT mutee_id FROM mutes WHERE muter_id = ?)
            -- Filter out low-engagement old posts unless it's someone we follow
            AND (
                p.likes_count > 0
                OR p.reposts_count > 0
                OR p.user_id IN (SELECT followee_id FROM follows WHERE follower_id = ?)
            )
            ORDER BY p.id DESC
            LIMIT ?
        """
        return self._paged_feed(query, (cursor, cursor, user_id, user_id, user_id, limit))

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
        return self._paged_feed(query, (cursor, cursor, limit))

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

    # Twitter's home feed is named "chronological_home"; the shared timeline blend in
    # SqliteSocialEngineBase reads this to source its follower posts.
    _follower_feed_strategy = "chronological_home"

    def get_timeline(
        self,
        strategy: str,
        username: str,
        limit: int = 10,
        recsys_type: str | None = None,
        **timeline_config: Any,
    ) -> list[dict]:
        """Add Twitter's ``curated_global`` strategy on top of the shared timeline blends."""
        if strategy == "curated_global":
            feed = self.get_feed("curated_global", username, limit=limit)
            return feed.get("posts", [])
        return super().get_timeline(
            strategy, username, limit, recsys_type=recsys_type, **timeline_config
        )

    # ================================================================ #
    # Extended social methods
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

    # ================================================================ #
    # Recommendation system
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

        rows: list[Any] = []
        for chunk in self._iter_in_chunks(user_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"""
                    SELECT user_id, content
                    FROM posts
                    WHERE user_id IN ({placeholders}) AND type != 'repost'
                    ORDER BY user_id ASC, created_at DESC, id DESC
                    """,
                    tuple(chunk),
                ).fetchall()
            )

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

        rows: list[Any] = []
        for chunk in self._iter_in_chunks(user_ids):
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                conn.execute(
                    f"""
                    SELECT l.user_id, p.content
                    FROM likes l
                    JOIN posts p ON p.id = l.post_id
                    WHERE l.user_id IN ({placeholders})
                    ORDER BY l.user_id ASC, l.created_at DESC, l.post_id DESC
                    """,
                    tuple(chunk),
                ).fetchall()
            )

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

    @staticmethod
    def _prune_user_context_cache(
        embeddings_cache: dict,
        live_user_keys: set[tuple[Any, ...]],
        live_post_ids: set[int] | None = None,
        scoped_user_ids: set[int] | None = None,
    ) -> None:
        """Drop stale cached embeddings after an update pass.

        User-context cache keys embed the full per-step context string (bio +
        recent/liked post snippets), so a user's key changes whenever their
        context changes. Without eviction these per-step keys accumulate without
        bound across update passes. Post embeddings (``post_emb`` keys) are
        immutable per post, but only the current candidate window is ever scored
        again — retaining a tensor for every post ever seen grows without bound
        over a long run, so keys outside ``live_post_ids`` are dropped too
        (``None`` retains all, for callers that don't cache post embeddings).
        ``scoped_user_ids`` limits user-key eviction to those users — a scoped
        (active-users-only) pass computes ``live_user_keys`` for the active
        subset only, so evicting against the whole cache would drop every
        inactive user's entry; evicting per-user keeps the cache bounded at one
        key per user without touching users outside the pass.
        """
        stale = [
            key
            for key in embeddings_cache
            if isinstance(key, tuple)
            and key
            and key[0] == "user"
            and key not in live_user_keys
            and (scoped_user_ids is None or (len(key) > 1 and key[1] in scoped_user_ids))
        ]
        if live_post_ids is not None:
            stale.extend(
                key
                for key in embeddings_cache
                if isinstance(key, tuple)
                and len(key) == 2
                and key[0] == "post_emb"
                and key[1] not in live_post_ids
            )
        for key in stale:
            del embeddings_cache[key]

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
    ) -> Any:
        """Load the embedding model for an explicitly configured recsys type.

        A missing dependency or a failed load is a CONFIG error, not a degraded
        run: silently returning None left a scenario that asked for
        ``recsys_type: twitter`` running with no recommender at all. Mirrors the
        strict twhin loader.
        """
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as err:
            raise RuntimeError(
                f"recsys_type '{recsys_type}' requires the optional 'sentence-transformers' "
                "package, which is not installed. Install it, or configure a recsys type "
                "that needs no embedding model (e.g. 'twitter_tfidf')."
            ) from err

        try:
            loaded_model = SentenceTransformer(model_name)
        except Exception as err:
            raise RuntimeError(
                f"Failed loading the '{recsys_type}' recsys model '{model_name}': {err}"
            ) from err
        logger.info("Loaded %s recsys model '%s'", recsys_type, model_name)
        return loaded_model

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
    ) -> int:
        """Update recommendations for all initialized algorithm types.

        Args:
            active_user_ids: Optional list of user IDs to limit updates to.
            max_posts: Maximum recommendations per user per algorithm.

        Generates recommendations for each initialized recsys type and stores them
        in the database with the algorithm type tagged. With ``active_user_ids``
        only those users' rows are replaced (everyone else's stay as-is) — the
        O(active) path; without it the whole table is cleared and recomputed.
        Returns the number of recommendation rows written this pass. A failure
        PROPAGATES: the scheduling component counts it as ``recsys_update_failures``
        rather than the caller logging a -1 sentinel into the action log.
        """
        if not hasattr(self, "_recsys_types") or not self._recsys_types:
            logger.warning(
                "update_recommendations called with no live recsys types; recommendations "
                "will not refresh. After a checkpoint restore the update component should "
                "re-initialize recsys (via recsys_active_types reconciliation)."
            )
            return 0

        with self.get_connection() as conn:
            # Get users
            user_rows: list[Any] = []
            if active_user_ids:
                for chunk in self._iter_in_chunks(active_user_ids):
                    placeholders = ",".join("?" * len(chunk))
                    user_rows.extend(
                        conn.execute(
                            f"SELECT id, username, bio FROM users WHERE id IN ({placeholders})",
                            chunk,
                        ).fetchall()
                    )
            else:
                user_rows = conn.execute("SELECT id, username, bio FROM users").fetchall()

            users = [{"id": r[0], "username": r[1], "bio": r[2]} for r in user_rows]
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
                return 0

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

            # Replace only the scoped users' rows (every live recsys type is
            # recomputed for them below); a full update clears the table.
            if active_user_ids:
                for chunk in self._iter_in_chunks(active_user_ids):
                    delete_placeholders = ",".join("?" * len(chunk))
                    conn.execute(
                        f"DELETE FROM recommendations WHERE user_id IN ({delete_placeholders})",
                        chunk,
                    )
            else:
                conn.execute("DELETE FROM recommendations")
            rows_written = 0

            # Generate recommendations for each algorithm type
            for recsys_type, state in self._recsys_types.items():
                logger.debug(f"Computing {recsys_type} recommendations for {len(users)} users")

                if recsys_type in ("twitter", "twhin", "twitter_tfidf"):
                    rec_matrix = self._rec_embedding(
                        users,
                        posts,
                        max_posts,
                        state,
                        # A scoped pass sees only the active users; cache
                        # eviction must stay scoped to them or every inactive
                        # user's cached context would be dropped each step.
                        scoped_user_ids=set(user_ids) if active_user_ids else None,
                    )
                else:
                    logger.warning(f"Unknown recsys_type '{recsys_type}'; skipping")
                    continue

                # Store recommendations with algorithm type (one executemany,
                # not N*max_posts single-row round-trips)
                cursor = conn.executemany(
                    "INSERT OR IGNORE INTO recommendations (user_id, post_id, recsys_type) VALUES (?, ?, ?)",
                    [
                        (user_id, post_id, recsys_type)
                        for user_id, post_ids in rec_matrix.items()
                        for post_id in post_ids
                    ],
                )
                rows_written += max(int(cursor.rowcount or 0), 0)

                logger.info(f"Updated {recsys_type} recommendations for {len(rec_matrix)} users")

        conn.commit()
        logger.info(
            f"Recommendations update complete for {len(self._recsys_types)} algorithm types"
        )
        return rows_written

    def _rec_embedding(
        self,
        users: list,
        posts: list,
        max_posts: int,
        recsys_state: dict | None = None,
        scoped_user_ids: set[int] | None = None,
    ) -> dict:
        """Embedding-based recommendations.

        Args:
            users: List of user dicts.
            posts: List of post dicts.
            max_posts: Maximum recommendations per user.
            recsys_state: State dict for this algorithm type containing model
                and embeddings_cache.
            scoped_user_ids: When the pass covers only the active users, their
                ids — cache eviction is then limited to those users.
        """
        if recsys_state is None:
            raise ValueError("Embedding recommendations require initialized recsys_state.")

        # Direct index: init_recsys always installs "model" for an embedding
        # backend, so a missing key is a broken state dict, not a "no model" case.
        model = recsys_state["model"]
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

        if backend == "tfidf":
            return self._rec_tfidf(users, posts, max_posts, user_context_index=user_context_index)

        if backend == "twhin_transformers":
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
                scoped_user_ids=scoped_user_ids,
            )

        try:
            import torch

            rec_matrix = {}

            # Posts are append-only, so batch-encode only ids not already in the
            # persistent embeddings_cache and reassemble in post order, avoiding
            # re-encoding the same posts on every scheduled recsys update.
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

            # Track user-context keys used this pass so stale per-step keys can
            # be evicted afterwards (see _prune_user_context_cache).
            live_user_keys: set[tuple[Any, ...]] = set()

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
                live_user_keys.add(cache_key)
                if cache_key not in embeddings_cache:
                    user_emb = model.encode(user_context, convert_to_tensor=True)
                    embeddings_cache[cache_key] = user_emb
                else:
                    user_emb = embeddings_cache[cache_key]

                # Compute similarities
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

            self._prune_user_context_cache(
                embeddings_cache,
                live_user_keys,
                live_post_ids=set(post_ids),
                scoped_user_ids=scoped_user_ids,
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
        scoped_user_ids: set[int] | None = None,
    ) -> dict:
        """TWHIN-BERT recommendations via transformers tokenizer/model."""
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

            # Track user-context keys used this pass so stale per-step keys can
            # be evicted afterwards (see _prune_user_context_cache).
            live_user_keys: set[tuple[Any, ...]] = set()

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
                live_user_keys.add(cache_key)
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

            self._prune_user_context_cache(
                embeddings_cache, live_user_keys, scoped_user_ids=scoped_user_ids
            )
            return rec_matrix
        except Exception as e:
            logger.error("Error in TWHIN recsys: %s", e, exc_info=True)
            return {}

    def _recommendation_posts(self, rows) -> list[dict]:
        """Twitter enriches recommendation rows via _parse_posts (base uses dict())."""
        return self._parse_posts(rows)
