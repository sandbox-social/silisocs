import os

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from mastodon_sim.environments.backends.twitter_like.engine import TwitterLikePlatform

app = FastAPI()
DB_PATH = os.getenv("TWITTER_LIKE_DB", "twitter_like.db")
platform = TwitterLikePlatform(DB_PATH, use_queue=True)

# Setup Templates
templates = Jinja2Templates(
    directory="src/mastodon_sim/environments/backends/twitter_like/visualizer/templates"
)


@app.on_event("shutdown")
def shutdown_event():
    platform.shutdown()


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/api/feed/global")
async def get_global_feed(limit: int = 50, cursor: int | None = None):
    # Depending on preference, can route to curated_global or firehose
    return platform.get_feed("curated_global", limit=limit, cursor=cursor)


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


@app.get("/api/user/{username}")
async def get_user_profile(username: str):
    profile = platform.view_profile(username)
    if not profile:
        return JSONResponse(status_code=404, content={"error": "User not found"})
    return profile


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8002)
