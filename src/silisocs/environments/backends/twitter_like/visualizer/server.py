"""FastAPI visualizer server for the Twitter-like simulation backend.

Provides a read-only web UI for inspecting simulation state. Studio mounts
``create_viewer_app`` in-process (see ``VisualizerSpec.app_factory``); the same
factory backs the standalone server:

    TWITTER_LIKE_DB=path/to/db.db python -m silisocs.environments.backends.twitter_like.visualizer.server

Page assets are addressed relatively so the app serves identically at ``/``
(standalone) and under a mount prefix (Studio).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from silisocs.environments.backends._viewer_common import build_viewer_app, run_standalone
from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_POST_COLUMNS = [
    "id",
    "user_id",
    "content",
    "created_at",
    "type",
    "reply_to_id",
    "quote_of_id",
    "likes_count",
    "reposts_count",
    "reply_count",
    "username",
]


def create_viewer_app(db_path: str | Path) -> FastAPI:  # noqa: C901, PLR0915 - a viewer is a flat list of read-only routes
    """Build an isolated read-only viewer over one run database."""
    platform = TwitterLikePlatform(str(db_path), use_queue=False)

    # The reply-tree walk in `/api/post/{id}/thread` and `/replies` filters on
    # `posts.reply_to_id`; without this index each node is a full table scan.
    # The canonical home for this is the twitter_like schema in engine.py; it is
    # created here defensively (best effort — a read-only DB simply keeps the
    # scan) because the viewer must stay correct even against older databases.
    with contextlib.suppress(Exception), platform.get_connection() as conn:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_posts_reply_to ON posts(reply_to_id)")

    def register_routes(app: FastAPI, platform: TwitterLikePlatform) -> None:  # noqa: C901, PLR0915
        # -------------------------------------------------------------------
        # Feed APIs
        # -------------------------------------------------------------------
        @app.get("/api/feed/global")
        def get_global_feed(limit: int = 50, cursor: int | None = None):
            return platform.get_feed("firehose", limit=limit, cursor=cursor)

        @app.get("/api/feed/home/{username}")
        def get_home_feed(username: str, limit: int = 50, cursor: int | None = None):
            try:
                return platform.get_feed(
                    "chronological_home", username=username, limit=limit, cursor=cursor
                )
            except ValueError as e:
                return JSONResponse(status_code=404, content={"error": str(e)})

        @app.get("/api/feed/user/{username}")
        def get_user_feed(username: str, limit: int = 50, cursor: int | None = None):
            return platform.get_feed("profile", username=username, limit=limit, cursor=cursor)

        # -------------------------------------------------------------------
        # Post detail & replies
        # -------------------------------------------------------------------
        @app.get("/api/post/{post_id}")
        def get_post(post_id: int):
            """Return a single post by ID."""
            with platform.get_connection() as conn:
                row = conn.execute(
                    """SELECT p.id, p.user_id, p.content, p.created_at, p.type,
                              p.reply_to_id, p.quote_of_id, p.likes_count,
                              p.reposts_count, p.reply_count, u.username
                       FROM posts p JOIN users u ON p.user_id = u.id
                       WHERE p.id = ?""",
                    (post_id,),
                ).fetchone()
                if not row:
                    return JSONResponse(status_code=404, content={"error": "Post not found"})
                return dict(zip(_POST_COLUMNS, row, strict=False))

        @app.get("/api/post/{post_id}/replies")
        def get_post_replies(post_id: int, limit: int = 50):
            """Return direct replies to a post."""
            with platform.get_connection() as conn:
                rows = conn.execute(
                    """SELECT p.id, p.user_id, p.content, p.created_at, p.type,
                              p.reply_to_id, p.quote_of_id, p.likes_count,
                              p.reposts_count, p.reply_count, u.username
                       FROM posts p JOIN users u ON p.user_id = u.id
                       WHERE p.reply_to_id = ?
                       ORDER BY p.id ASC LIMIT ?""",
                    (post_id, limit),
                ).fetchall()
                return {"replies": [dict(zip(_POST_COLUMNS, r, strict=False)) for r in rows]}

        @app.get("/api/post/{post_id}/thread")
        def get_post_thread(post_id: int):
            """Return the full reply thread for a post (ancestors + descendants)."""
            with platform.get_connection() as conn:
                # Walk up to find root
                current_id = post_id
                while True:
                    row = conn.execute(
                        "SELECT reply_to_id FROM posts WHERE id = ?", (current_id,)
                    ).fetchone()
                    if not row or not row[0]:
                        break
                    current_id = row[0]
                root_id = current_id

                # BFS to get all descendants
                all_ids = [root_id]
                queue = [root_id]
                while queue:
                    pid = queue.pop(0)
                    children = conn.execute(
                        "SELECT id FROM posts WHERE reply_to_id = ?", (pid,)
                    ).fetchall()
                    for (cid,) in children:
                        all_ids.append(cid)
                        queue.append(cid)

                placeholders = ",".join("?" * len(all_ids))
                rows = conn.execute(
                    f"""SELECT p.id, p.user_id, p.content, p.created_at, p.type,
                               p.reply_to_id, p.quote_of_id, p.likes_count,
                               p.reposts_count, p.reply_count, u.username
                        FROM posts p JOIN users u ON p.user_id = u.id
                        WHERE p.id IN ({placeholders})
                        ORDER BY p.id ASC""",
                    all_ids,
                ).fetchall()
                return {
                    "thread": [dict(zip(_POST_COLUMNS, r, strict=False)) for r in rows],
                    "root_id": root_id,
                    "focus_id": post_id,
                }

        # -------------------------------------------------------------------
        # User APIs
        # -------------------------------------------------------------------
        @app.get("/api/user/{username}")
        def get_user_profile(username: str):
            profile = platform.view_profile(username)
            if not profile:
                return JSONResponse(status_code=404, content={"error": "User not found"})
            return profile

        @app.get("/api/user/{username}/followers")
        def get_followers(username: str, limit: int = 50):
            with platform.get_connection() as conn:
                uid = platform.get_user_id(username)
                if uid is None:
                    return JSONResponse(status_code=404, content={"error": "User not found"})
                rows = conn.execute(
                    """SELECT u.username, u.bio, u.posts_count, u.followers_count
                       FROM follows f JOIN users u ON f.follower_id = u.id
                       WHERE f.followee_id = ? LIMIT ?""",
                    (uid, limit),
                ).fetchall()
                return {
                    "followers": [
                        {
                            "username": r[0],
                            "bio": r[1],
                            "posts_count": r[2],
                            "followers_count": r[3],
                        }
                        for r in rows
                    ]
                }

        @app.get("/api/user/{username}/following")
        def get_following(username: str, limit: int = 50):
            with platform.get_connection() as conn:
                uid = platform.get_user_id(username)
                if uid is None:
                    return JSONResponse(status_code=404, content={"error": "User not found"})
                rows = conn.execute(
                    """SELECT u.username, u.bio, u.posts_count, u.followers_count
                       FROM follows f JOIN users u ON f.followee_id = u.id
                       WHERE f.follower_id = ? LIMIT ?""",
                    (uid, limit),
                ).fetchall()
                return {
                    "following": [
                        {
                            "username": r[0],
                            "bio": r[1],
                            "posts_count": r[2],
                            "followers_count": r[3],
                        }
                        for r in rows
                    ]
                }

        @app.get("/api/users")
        def list_users(limit: int = 100):
            """List all users ordered by post count."""
            with platform.get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, username, bio, posts_count, followers_count, following_count "
                    "FROM users ORDER BY posts_count DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return {
                    "users": [
                        {
                            "id": r[0],
                            "username": r[1],
                            "bio": r[2],
                            "posts_count": r[3],
                            "followers_count": r[4],
                            "following_count": r[5],
                        }
                        for r in rows
                    ]
                }

        @app.get("/api/users/search")
        def search_users(q: str, limit: int = 20):
            results = platform.search_users(q, limit=limit)
            return {"users": results}

        # -------------------------------------------------------------------
        # Stats (admin/global view)
        # -------------------------------------------------------------------
        @app.get("/api/stats")
        def get_stats():
            """Global platform statistics for the admin overview."""
            with platform.get_connection() as conn:
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                post_count = conn.execute(
                    "SELECT COUNT(*) FROM posts WHERE type = 'post'"
                ).fetchone()[0]
                reply_count = conn.execute(
                    "SELECT COUNT(*) FROM posts WHERE reply_to_id IS NOT NULL"
                ).fetchone()[0]
                like_count = conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
                repost_count = conn.execute(
                    "SELECT COUNT(*) FROM posts WHERE type IN ('repost','quote')"
                ).fetchone()[0]
                follow_count = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
                top_users = conn.execute(
                    "SELECT username, posts_count, followers_count FROM users "
                    "ORDER BY posts_count DESC LIMIT 10"
                ).fetchall()
                top_posts = conn.execute(
                    """SELECT p.id, u.username, p.content, p.likes_count, p.reposts_count,
                              p.reply_count
                       FROM posts p JOIN users u ON p.user_id = u.id
                       WHERE p.type = 'post'
                       ORDER BY p.likes_count DESC LIMIT 10"""
                ).fetchall()
                return {
                    "user_count": user_count,
                    "post_count": post_count,
                    "reply_count": reply_count,
                    "like_count": like_count,
                    "repost_count": repost_count,
                    "follow_count": follow_count,
                    "top_users": [
                        {"username": r[0], "posts_count": r[1], "followers_count": r[2]}
                        for r in top_users
                    ],
                    "top_posts": [
                        {
                            "id": r[0],
                            "username": r[1],
                            "content": r[2][:200],
                            "likes_count": r[3],
                            "reposts_count": r[4],
                            "reply_count": r[5],
                        }
                        for r in top_posts
                    ],
                }

    return build_viewer_app(
        platform,
        title="Twitter-like Simulation Visualizer",
        template_dir=_TEMPLATE_DIR,
        register_routes=register_routes,
    )


if __name__ == "__main__":
    run_standalone(
        create_viewer_app,
        db_env_var="TWITTER_LIKE_DB",
        default_db="twitter_like.db",
        default_port=8002,
    )
