"""FastAPI visualizer server for the Twitter-like simulation backend.

Provides a read-only web UI for inspecting simulation state.
Run standalone:
    TWITTER_LIKE_DB=path/to/db.db python -m silisocs.environments.backends.twitter_like.visualizer.server
"""

from __future__ import annotations

import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from silisocs.environments.backends.twitter_like.engine import TwitterLikePlatform

app = FastAPI(title="Twitter-like Simulation Visualizer")
DB_PATH = os.getenv("TWITTER_LIKE_DB", "twitter_like.db")
platform = TwitterLikePlatform(DB_PATH, use_queue=False)

_TEMPLATE_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@app.on_event("shutdown")
def shutdown_event():
    """shutdown_event.
    """

    platform.shutdown()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


# ---------------------------------------------------------------------------
# Feed APIs
# ---------------------------------------------------------------------------
@app.get("/api/feed/global")
async def get_global_feed(limit: int = 50, cursor: int | None = None):
    return platform.get_feed("firehose", limit=limit, cursor=cursor)


@app.get("/api/feed/home/{username}")
async def get_home_feed(username: str, limit: int = 50, cursor: int | None = None):
    try:
        return platform.get_feed(
            "chronological_home", username=username, limit=limit, cursor=cursor
        )
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.get("/api/feed/user/{username}")
async def get_user_feed(username: str, limit: int = 50, cursor: int | None = None):
    return platform.get_feed("profile", username=username, limit=limit, cursor=cursor)


# ---------------------------------------------------------------------------
# Post detail & replies
# ---------------------------------------------------------------------------
@app.get("/api/post/{post_id}")
async def get_post(post_id: int):
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
        cols = [
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
        return dict(zip(cols, row))


@app.get("/api/post/{post_id}/replies")
async def get_post_replies(post_id: int, limit: int = 50):
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
        cols = [
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
        return {"replies": [dict(zip(cols, r)) for r in rows]}


@app.get("/api/post/{post_id}/thread")
async def get_post_thread(post_id: int):
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
            children = conn.execute("SELECT id FROM posts WHERE reply_to_id = ?", (pid,)).fetchall()
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
        cols = [
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
        return {
            "thread": [dict(zip(cols, r)) for r in rows],
            "root_id": root_id,
            "focus_id": post_id,
        }


# ---------------------------------------------------------------------------
# User APIs
# ---------------------------------------------------------------------------
@app.get("/api/user/{username}")
async def get_user_profile(username: str):
    profile = platform.view_profile(username)
    if not profile:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return profile


@app.get("/api/user/{username}/followers")
async def get_followers(username: str, limit: int = 50):
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
                {"username": r[0], "bio": r[1], "posts_count": r[2], "followers_count": r[3]}
                for r in rows
            ]
        }


@app.get("/api/user/{username}/following")
async def get_following(username: str, limit: int = 50):
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
                {"username": r[0], "bio": r[1], "posts_count": r[2], "followers_count": r[3]}
                for r in rows
            ]
        }


@app.get("/api/users")
async def list_users(limit: int = 100):
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
async def search_users(q: str, limit: int = 20):
    results = platform.search_users(q, limit=limit)
    return {"users": results}


# ---------------------------------------------------------------------------
# Stats (admin/global view)
# ---------------------------------------------------------------------------
@app.get("/api/stats")
async def get_stats():
    """Global platform statistics for the admin overview."""
    with platform.get_connection() as conn:
        user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        post_count = conn.execute("SELECT COUNT(*) FROM posts WHERE type = 'post'").fetchone()[0]
        reply_count = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE reply_to_id IS NOT NULL"
        ).fetchone()[0]
        like_count = conn.execute("SELECT COUNT(*) FROM likes").fetchone()[0]
        repost_count = conn.execute(
            "SELECT COUNT(*) FROM posts WHERE type IN ('repost','quote')"
        ).fetchone()[0]
        follow_count = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
        top_users = conn.execute(
            "SELECT username, posts_count, followers_count FROM users ORDER BY posts_count DESC LIMIT 10"
        ).fetchall()
        top_posts = conn.execute(
            """SELECT p.id, u.username, p.content, p.likes_count, p.reposts_count, p.reply_count
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
                {"username": r[0], "posts_count": r[1], "followers_count": r[2]} for r in top_users
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
