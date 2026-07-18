"""FastAPI visualizer server for the Reddit-like simulation backend.

Studio mounts ``create_viewer_app`` in-process (see ``VisualizerSpec.app_factory``);
the same factory backs the standalone server:

    REDDIT_LIKE_DB=path/to/db.db python -m silisocs.environments.backends.reddit_like.visualizer.server

Page assets are addressed relatively so the app serves identically at ``/``
(standalone) and under a mount prefix (Studio).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from silisocs.environments.backends._viewer_common import build_viewer_app, run_standalone
from silisocs.environments.backends.reddit_like.engine import RedditLikePlatform

_TEMPLATE_DIR = Path(__file__).parent / "templates"
_POST_COLUMNS = [
    "id",
    "user_id",
    "username",
    "subreddit_id",
    "subreddit_name",
    "title",
    "content",
    "created_at",
    "upvotes",
    "downvotes",
    "comment_count",
]


def create_viewer_app(db_path: str | Path) -> FastAPI:  # noqa: C901, PLR0915 - a viewer is a flat list of read-only routes
    """Build an isolated read-only viewer over one run database."""
    platform = RedditLikePlatform(str(db_path), use_queue=False)

    def register_routes(app: FastAPI, platform: RedditLikePlatform) -> None:  # noqa: C901, PLR0915
        # -------------------------------------------------------------------
        # Feed APIs
        # -------------------------------------------------------------------
        @app.get("/api/feed/home/{username}")
        def get_home_feed(username: str, limit: int = 50, cursor: int | None = None):
            try:
                return platform.get_feed("home", username=username, limit=limit, cursor=cursor)
            except ValueError as e:
                return JSONResponse(status_code=404, content={"error": str(e)})

        @app.get("/api/feed/popular")
        def get_popular_feed(limit: int = 50, cursor: int | None = None):
            return platform.get_feed("popular", limit=limit, cursor=cursor)

        @app.get("/api/feed/subreddit/{sub_name}")
        def get_subreddit_feed(sub_name: str, limit: int = 50, cursor: int | None = None):
            try:
                return platform.get_subreddit_feed(sub_name, limit=limit, cursor=cursor)
            except ValueError as e:
                return JSONResponse(status_code=404, content={"error": str(e)})

        @app.get("/api/feed/user/{username}")
        def get_user_feed(username: str, limit: int = 50, cursor: int | None = None):
            try:
                return platform.get_user_feed(username, limit=limit, cursor=cursor)
            except ValueError as e:
                return JSONResponse(status_code=404, content={"error": str(e)})

        @app.get("/api/feed/new")
        def get_new_feed(limit: int = 50, cursor: int | None = None):
            """All posts newest-first (global timeline)."""
            with platform.get_connection() as conn:
                query = (
                    "SELECT p.id, p.user_id, u.username, p.subreddit_id, "
                    "s.name as subreddit_name, p.title, p.content, p.created_at, "
                    "p.upvotes, p.downvotes, p.comment_count "
                    "FROM posts p JOIN users u ON p.user_id = u.id "
                    "JOIN subreddits s ON p.subreddit_id = s.id"
                )
                params: list = []
                if cursor:
                    query += " WHERE p.id < ?"
                    params.append(cursor)
                query += " ORDER BY p.id DESC LIMIT ?"
                params.append(limit)
                rows = conn.execute(query, params).fetchall()
                posts = []
                for r in rows:
                    d = dict(zip(_POST_COLUMNS, r, strict=False))
                    d["score"] = d["upvotes"] - d["downvotes"]
                    posts.append(d)
                next_cursor = posts[-1]["id"] if posts else None
                return {"posts": posts, "next_cursor": next_cursor}

        # -------------------------------------------------------------------
        # Post & comments
        # -------------------------------------------------------------------
        @app.get("/api/post/{post_id}")
        def get_post(post_id: int):
            """Return a single post."""
            with platform.get_connection() as conn:
                row = conn.execute(
                    """SELECT p.id, p.user_id, u.username, p.subreddit_id, s.name as subreddit_name,
                              p.title, p.content, p.created_at, p.upvotes, p.downvotes,
                              p.comment_count
                       FROM posts p JOIN users u ON p.user_id = u.id
                       JOIN subreddits s ON p.subreddit_id = s.id
                       WHERE p.id = ?""",
                    (post_id,),
                ).fetchone()
                if not row:
                    return JSONResponse(status_code=404, content={"error": "Post not found"})
                d = dict(zip(_POST_COLUMNS, row, strict=False))
                d["score"] = d["upvotes"] - d["downvotes"]
                return d

        @app.get("/api/post/{post_id}/comments")
        def get_comments(post_id: int):
            try:
                comments = platform.get_post_comments(post_id)
                return {"comments": comments}
            except Exception as e:
                return JSONResponse(status_code=500, content={"error": str(e)})

        # -------------------------------------------------------------------
        # User APIs
        # -------------------------------------------------------------------
        @app.get("/api/user/{username}")
        def get_user_profile(username: str):
            profile = platform.view_profile(username)
            if not profile:
                return JSONResponse(status_code=404, content={"error": "User not found"})
            return profile

        @app.get("/api/users")
        def list_users(limit: int = 100):
            with platform.get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, username, bio, karma FROM users ORDER BY karma DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return {
                    "users": [
                        {"id": r[0], "username": r[1], "bio": r[2], "karma": r[3]} for r in rows
                    ]
                }

        @app.get("/api/users/search")
        def search_users(q: str, limit: int = 20):
            with platform.get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, username, bio, karma FROM users WHERE username LIKE ? LIMIT ?",
                    (f"%{q}%", limit),
                ).fetchall()
                return {
                    "users": [
                        {"id": r[0], "username": r[1], "bio": r[2], "karma": r[3]} for r in rows
                    ]
                }

        # -------------------------------------------------------------------
        # Subreddit APIs
        # -------------------------------------------------------------------
        @app.get("/api/subreddits")
        def list_subreddits():
            with platform.get_connection() as conn:
                rows = conn.execute(
                    "SELECT id, name, description, members_count FROM subreddits "
                    "ORDER BY members_count DESC"
                ).fetchall()
                return {
                    "subreddits": [
                        {"id": r[0], "name": r[1], "description": r[2], "members_count": r[3]}
                        for r in rows
                    ]
                }

        @app.get("/api/subreddit/{name}")
        def get_subreddit_info(name: str):
            with platform.get_connection() as conn:
                row = conn.execute(
                    "SELECT id, name, description, members_count, created_at FROM subreddits "
                    "WHERE name = ?",
                    (name,),
                ).fetchone()
                if not row:
                    return JSONResponse(status_code=404, content={"error": "Subreddit not found"})
                return {
                    "id": row[0],
                    "name": row[1],
                    "description": row[2],
                    "members_count": row[3],
                    "created_at": row[4],
                }

        # -------------------------------------------------------------------
        # Stats (admin/global view)
        # -------------------------------------------------------------------
        @app.get("/api/stats")
        def get_stats():
            with platform.get_connection() as conn:
                user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                post_count = conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]
                comment_count = conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0]
                sub_count = conn.execute("SELECT COUNT(*) FROM subreddits").fetchone()[0]
                vote_count = conn.execute("SELECT COUNT(*) FROM votes").fetchone()[0]
                top_users = conn.execute(
                    "SELECT username, karma FROM users ORDER BY karma DESC LIMIT 10"
                ).fetchall()
                top_posts = conn.execute(
                    """SELECT p.id, u.username, s.name, p.title, p.upvotes-p.downvotes as score,
                              p.comment_count
                       FROM posts p JOIN users u ON p.user_id = u.id
                       JOIN subreddits s ON p.subreddit_id = s.id
                       ORDER BY score DESC LIMIT 10"""
                ).fetchall()
                top_subs = conn.execute(
                    "SELECT name, members_count FROM subreddits ORDER BY members_count DESC LIMIT 10"
                ).fetchall()
                return {
                    "user_count": user_count,
                    "post_count": post_count,
                    "comment_count": comment_count,
                    "subreddit_count": sub_count,
                    "vote_count": vote_count,
                    "top_users": [{"username": r[0], "karma": r[1]} for r in top_users],
                    "top_posts": [
                        {
                            "id": r[0],
                            "username": r[1],
                            "subreddit": r[2],
                            "title": r[3][:100],
                            "score": r[4],
                            "comment_count": r[5],
                        }
                        for r in top_posts
                    ],
                    "top_subreddits": [{"name": r[0], "members_count": r[1]} for r in top_subs],
                }

    return build_viewer_app(
        platform,
        title="Reddit-like Simulation Visualizer",
        template_dir=_TEMPLATE_DIR,
        register_routes=register_routes,
    )


if __name__ == "__main__":
    run_standalone(
        create_viewer_app,
        db_env_var="REDDIT_LIKE_DB",
        default_db="reddit_like.db",
        default_port=8001,
    )
