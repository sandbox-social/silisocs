import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mastodon_sim.environments.backends.reddit_like.engine import RedditLikePlatform

app = FastAPI()
DB_PATH = os.getenv("REDDIT_LIKE_DB", "reddit_like.db")
platform = RedditLikePlatform(DB_PATH, use_queue=True)

# Setup Templates
templates = Jinja2Templates(
    directory="src/mastodon_sim/environments/backends/reddit_like/visualizer/templates"
)


@app.on_event("shutdown")
def shutdown_event():
    platform.shutdown()


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/feed/home/{username}")
async def get_home_feed(username: str, limit: int = 50, cursor: int | None = None):
    try:
        return platform.get_feed("home", username=username, limit=limit, cursor=cursor)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.get("/api/feed/popular")
async def get_popular_feed(limit: int = 50, cursor: int | None = None):
    return platform.get_feed("popular", limit=limit, cursor=cursor)


@app.get("/api/feed/subreddit/{sub_name}")
async def get_subreddit_feed(sub_name: str, limit: int = 50, cursor: int | None = None):
    try:
        return platform.get_subreddit_feed(sub_name, limit=limit, cursor=cursor)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.get("/api/feed/user/{username}")
async def get_user_feed(username: str, limit: int = 50, cursor: int | None = None):
    try:
        return platform.get_user_feed(username, limit=limit, cursor=cursor)
    except ValueError as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.get("/api/post/{post_id}/comments")
async def get_comments(post_id: int):
    try:
        comments = platform.get_post_comments(post_id)
        return {"comments": comments}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.get("/api/user/{username}")
async def get_user_profile(username: str):
    profile = platform.view_profile(username)
    if not profile:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return profile


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8001)
