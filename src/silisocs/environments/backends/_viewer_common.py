"""Shared scaffold for the backend platform viewer servers.

The microblog (``twitter_like``) and forum (``reddit_like``) viewers differ only
in their platform class, page title, standalone env var / port, and their own set
of read-only API routes. Everything else — the read-only lifespan, the
``close_viewer`` handle a mounted sub-app needs, the index page, and the cached
``/assets/design.css`` + ``/assets/viewer.js`` routes — is identical, so it lives
here and each ``server.py`` becomes a thin call to :func:`build_viewer_app`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from silisocs.design.assets import CachedAsset
from silisocs.design.css import viewer_script, viewer_stylesheet


def _cached_asset_route(app: FastAPI, path: str, asset: CachedAsset) -> None:
    """Register a GET route that serves one immutable body with ETag handling.

    The 304-vs-200 decision is owned by :meth:`CachedAsset.response` (framework
    neutral); this only turns its ``(status, body, headers)`` tuple into the
    surface's own ``Response``.
    """

    @app.get(path, response_class=Response)
    def serve(request: Request) -> Response:
        status, body, headers = asset.response(request.headers.get("if-none-match"))
        if body is None:
            return Response(status_code=status, headers=headers)
        return Response(body, status_code=status, media_type=asset.media_type, headers=headers)


def build_viewer_app(
    platform: Any,
    *,
    title: str,
    template_dir: str | Path,
    register_routes: Callable[[FastAPI, Any], None],
) -> FastAPI:
    """Build a read-only viewer FastAPI app around ``platform``.

    ``register_routes(app, platform)`` adds the backend-specific API routes; it
    should declare its DB-touching handlers as plain ``def`` (not ``async def``)
    so Starlette offloads their synchronous SQLite work to the threadpool instead
    of blocking the event loop the viewer shares with its Studio host.
    """
    templates = Jinja2Templates(directory=str(template_dir))
    stylesheet = CachedAsset.build(viewer_stylesheet(), "text/css")
    script = CachedAsset.build(viewer_script(), "text/javascript")
    font = CachedAsset.build_bytes(
        (
            Path(__file__).parents[2] / "design" / "fonts" / "manrope-latin-variable.woff2"
        ).read_bytes(),
        "font/woff2",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        """Release the read-only platform connection when the viewer stops."""
        yield
        platform.shutdown()

    app = FastAPI(title=title, lifespan=lifespan)
    # A mounted sub-app never receives lifespan events, so the host (Studio)
    # closes the connection through this handle instead.
    app.state.close_viewer = platform.shutdown

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        return templates.TemplateResponse(request, "index.html")

    _cached_asset_route(app, "/assets/design.css", stylesheet)
    _cached_asset_route(app, "/assets/viewer.js", script)
    _cached_asset_route(app, "/assets/manrope.woff2", font)

    register_routes(app, platform)
    return app


def run_standalone(
    app_factory: Callable[[str], FastAPI],
    *,
    db_env_var: str,
    default_db: str,
    default_port: int,
) -> None:
    """Serve a viewer standalone (the ``python -m ...server`` entry point).

    Binds loopback by default: the viewer serves an unauthenticated read view of
    the full run database and Studio only ever addresses it as loopback. Set
    ``SILISOCS_VIEWER_HOST`` to override for a deliberate LAN exposure.
    """
    import uvicorn  # noqa: PLC0415 - only needed for standalone serving, not when Studio mounts the app

    uvicorn.run(
        app_factory(os.getenv(db_env_var, default_db)),
        host=os.environ.get("SILISOCS_VIEWER_HOST", "127.0.0.1"),
        port=int(os.environ.get("SILISOCS_VIEWER_PORT", str(default_port))),
    )
