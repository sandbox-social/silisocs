"""API-first FastAPI shell for Silisocs Studio.

Routes live in ``silisocs.studio.routes`` (one module per area) and read the
shared repositories/managers off ``app.state``, which this module populates.
"""
# ruff: noqa: C901, PLC0415

import asyncio
import importlib
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from silisocs.design.assets import CachedAsset
from silisocs.design.css import css_variables
from silisocs.studio.jobs import JobManager
from silisocs.studio.plugins import load_studio_pages
from silisocs.studio.studies import StudyRepository
from silisocs.studio.viewers import ViewerDispatch
from silisocs.studio.workspace import WorkspaceCatalog

# How long a browser navigation waits for the warm-up before it is answered with
# the warming screen instead. A workspace small enough to index inside the grace
# (a test fixture, a small project) never shows the screen at all.
_WARMUP_GRACE_SECONDS = 0.5


class StudioWarmup:
    """The first-launch work a page load would otherwise block on.

    Two things are slow exactly once: indexing the workspace's extensions reads
    every project module, and the scenario composer drags in the engine import
    tree. Both are sub-second on a local disk and take a minute or more on a
    networked filesystem, so they run here on a background thread while the
    server binds and answers.
    """

    def __init__(self, workspace: WorkspaceCatalog) -> None:
        self._workspace = workspace
        self._done = threading.Event()
        self.phase = "Starting"

    @property
    def ready(self) -> bool:
        """Whether the warm-up has finished (successfully or not)."""
        return self._done.is_set()

    def wait(self, timeout: float) -> bool:
        """Block up to ``timeout`` seconds; ``True`` once the warm-up finished."""
        return self._done.wait(timeout)

    def start(self) -> None:
        """Run the warm-up on a daemon thread."""
        threading.Thread(target=self._run, daemon=True, name="studio-warmup").start()

    def _run(self) -> None:
        # Nothing is swallowed: a failure prints the thread's traceback, and the
        # request that needs the work re-runs it and raises to the caller. The
        # gate opens either way, so a broken workspace never hangs the server.
        try:
            self.phase = "Loading the scenario composer"
            importlib.import_module("silisocs.studio.forms")
            self.phase = "Indexing workspace extensions"
            self._workspace.extension_catalog()
        finally:
            self.phase = "Ready"
            self._done.set()


def _is_page_load(request) -> bool:
    """Whether a request is a browser navigation — the only thing the gate holds.

    API clients and the assets a page pulls in are never held: they either do
    not need the warm workspace or block on the same lock they always did.
    """
    return (
        request.method == "GET"
        and not request.url.path.startswith("/api")
        and "text/html" in request.headers.get("accept", "")
    )


def create_app(
    output_root: str | Path = "outputs",
    *,
    state_dir: str | Path | None = None,
    repo_root: str | Path = ".",
    max_concurrent_runs: int = 1,
    scenario_repositories: tuple[str | Path, ...] = (),
):
    """Create Studio, keeping FastAPI/Jinja imports inside the optional extra."""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import RedirectResponse, Response
        from fastapi.templating import Jinja2Templates
    except ImportError as exc:
        raise RuntimeError('Silisocs Studio requires: pip install "silisocs[studio]"') from exc

    # Imported here, not at module scope: every router module imports FastAPI.
    from silisocs.studio.routes import analysis, pages, run_page, runs
    from silisocs.studio.routes import jobs as job_routes
    from silisocs.studio.routes import scenarios as scenario_routes
    from silisocs.studio.routes import studies as study_routes
    from silisocs.studio.routes import viewers as viewer_routes

    root = Path(output_root).resolve()
    repository = Path(repo_root).resolve()
    configured_state = state_dir or os.environ.get("SILISOCS_STUDIO_STATE")
    studio_state = Path(configured_state or repository / ".silisocs").expanduser()
    jobs = JobManager(
        studio_state,
        output_root=root,
        max_concurrent_runs=max_concurrent_runs,
    )
    workspace = WorkspaceCatalog(
        repository,
        repository / ".silisocs",
        additional=scenario_repositories,
    )
    plugin_pages = load_studio_pages()
    package = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(package / "templates"))
    templates.env.globals["plugin_pages"] = plugin_pages

    viewers = ViewerDispatch(root)
    # Started here, not in the lifespan: the server must be answering while it
    # warms, and a plain (lifespan-less) ``TestClient`` gets the same app.
    warmup = StudioWarmup(workspace)
    warmup.start()

    @asynccontextmanager
    async def lifespan(_app):
        yield
        # Mounted sub-apps never see lifespan events, so their database
        # connections are closed through the dispatcher.
        viewers.close()
        jobs.close()

    app = FastAPI(
        title="Silisocs Studio",
        version="1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    # Everything the routers need, resolved once. They read it as
    # `request.app.state.<name>`, so nothing here is a module-level singleton.
    app.state.output_root = root
    app.state.repo_root = repository
    app.state.studio_state = studio_state
    app.state.jobs = jobs
    app.state.workspace = workspace
    app.state.scenarios = workspace.scenario_repository()
    app.state.studies = StudyRepository(repository / "experiments" / "studies")
    app.state.plugin_pages = plugin_pages
    app.state.templates = templates
    app.state.warmup = warmup

    # Read once at startup: these bodies only change when the package does.
    # Brand custom properties (light + dark) come from silisocs.design — Python
    # stays the single source of truth; studio.css holds layout only.
    app.state.assets = {
        "tokens.css": CachedAsset.build(
            css_variables() + (package / "static" / "studio.css").read_text(encoding="utf-8"),
            "text/css",
        ),
        "plotly.js": CachedAsset.build(
            (package / "static" / "plotly.min.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        "cytoscape.js": CachedAsset.build(
            (package / "static" / "cytoscape.min.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        # Studio's own scripts: boot/shell/panels plus one module per page area.
        # The vendored ``*.min.js`` bundles are served above under stable names.
        **{
            path.name: CachedAsset.build(path.read_text(encoding="utf-8"), "text/javascript")
            for path in sorted((package / "static").glob("*.js"))
            if not path.name.endswith(".min.js")
        },
        "manrope.woff2": CachedAsset.build_bytes(
            (package.parent / "design" / "fonts" / "manrope-latin-variable.woff2").read_bytes(),
            "font/woff2",
        ),
    }

    for page in plugin_pages:
        app.include_router(page.router)

    # Registered BEFORE the auth middleware on purpose: Starlette runs the
    # last-registered middleware outermost, so the warming screen is served
    # behind `protect_control_plane`, never in front of it.
    @app.middleware("http")
    async def hold_pages_until_warm(request: Request, call_next):
        if warmup.ready or not _is_page_load(request):
            return await call_next(request)
        if await asyncio.to_thread(warmup.wait, _WARMUP_GRACE_SECONDS):
            return await call_next(request)
        return templates.TemplateResponse(
            request,
            "warming.html",
            {"phase": warmup.phase},
            status_code=503,
            headers={"Retry-After": "1", "Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def protect_control_plane(request: Request, call_next):
        token = os.environ.get("STUDIO_AUTH_TOKEN", "")
        client_host = request.client.host if request.client else ""
        local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
        # A browser cannot attach the Bearer header on page loads, so the token
        # may also arrive once as ?token=<value> (set as a cookie below) or via
        # that cookie on subsequent requests.
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        via_query = request.query_params.get("token", "")
        authorized = bool(token) and token in (
            supplied,
            request.cookies.get("studio_token", ""),
            via_query,
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # CSRF: a browser attaches Origin to every cross-origin POST, so a
            # cross-site page cannot forge a control-plane call at localhost.
            # Same-origin Studio fetches and header-less clients (curl) pass.
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
                return Response("Cross-site control-plane request rejected", status_code=403)
            if (token and not authorized) or (not token and not local):
                return Response("Studio control-plane authorization required", status_code=401)
        elif token and not local and not authorized:
            # Reads leak run configs and logs; beyond localhost they need the
            # same token as mutations (localhost reads stay open by design).
            return Response("Studio authorization required", status_code=401)
        if token and via_query == token and request.method == "GET":
            # Set the session cookie, then redirect to the same URL WITHOUT the
            # token so the secret never lands in access logs, history, or
            # copied links.
            stripped = urlencode(
                [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
            )
            target = request.url.path + (f"?{stripped}" if stripped else "")
            response: Response = RedirectResponse(target, status_code=303)
            response.set_cookie("studio_token", token, httponly=True, samesite="lax")
            return response
        response = await call_next(request)
        if token and via_query == token:
            response.set_cookie("studio_token", token, httponly=True, samesite="lax")
        return response

    # Registration order is the matching order: plugin pages first (a plugin may
    # own a path Studio also serves), then the areas. `runs` is last because its
    # /api/runs/{run_id:path} catch-all would otherwise shadow the run-scoped
    # analysis routes.
    app.include_router(pages.router)
    app.include_router(run_page.router)
    app.include_router(scenario_routes.router)
    app.include_router(study_routes.router)
    app.include_router(analysis.router)
    app.include_router(job_routes.router)
    app.include_router(viewer_routes.router)
    app.include_router(runs.router)

    # Mounted last: a mount matches by prefix, so it must not shadow the routes
    # above. Viewer apps are built on first request, not here — runs are
    # discovered per request.
    app.mount("/viewers", viewers)
    app.state.viewers = viewers

    return app
