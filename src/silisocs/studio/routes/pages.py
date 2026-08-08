"""The shell pages that belong to no single domain, plus the assets they load."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

# Imported for its registration side effects: the shipped scenario schema and
# its choice/preview providers are declared there, and /settings lists them.
# It is deliberately cheap to import — the composer's engine-backed half lives
# in silisocs.studio.preflight, which nothing on the bind path touches.
import silisocs.studio.form_providers  # noqa: F401
from silisocs.analysis.panel import list_panels
from silisocs.analysis.views import BUILTIN_VIEWS
from silisocs.studio.form_schema import (
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
)
from silisocs.studio.routes.lookups import discover_all_runs
from silisocs.studio.state import studio_state

router = APIRouter()


@router.get("/assets/{name}")
def asset(request: Request, name: str):
    cached = studio_state(request).assets.get(name)
    if cached is None:
        raise HTTPException(status_code=404, detail=f"Unknown asset {name!r}")
    status, body, headers = cached.response(request.headers.get("if-none-match"))
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=cached.media_type, headers=headers)


@router.get("/api/ready")
def ready(request: Request):
    """Warm-up state. The warming screen polls this and reloads when ready."""
    warmup = studio_state(request).warmup
    return {"ready": warmup.ready, "phase": warmup.phase}


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    state = studio_state(request)
    runs = discover_all_runs(state)
    return state.templates.TemplateResponse(
        request,
        "home.html",
        {
            "runs": runs[:8],
            "all_runs": runs,
            "active": "home",
            "plugin_pages": state.plugin_pages,
            "scenario_count": len(state.workspace.scenarios()),
            "study_count": state.studies.count(),
            "live_count": len(
                [job for job in state.jobs.store.list() if job.status in {"queued", "running"}]
            ),
        },
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    state = studio_state(request)
    return state.templates.TemplateResponse(
        request,
        "settings.html",
        {
            "panels": list_panels(),
            "views": BUILTIN_VIEWS,
            "plugin_pages": state.plugin_pages,
            "form_schemas": list_form_schemas(),
            "choice_providers": list_choice_providers(),
            "preview_providers": list_preview_providers(),
            "repositories": [source.to_dict() for source in state.workspace.sources],
            "extension_catalog": state.workspace.extension_catalog(),
            "discovery_errors": state.workspace.discovery_errors,
            "active": "settings",
        },
    )
