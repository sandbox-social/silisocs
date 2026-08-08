"""The composer's own API: compose, preflight, and on-demand form capabilities."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

import asyncio

import yaml
from fastapi import APIRouter, HTTPException, Request

from silisocs.studio.compose import compose_files, field_values
from silisocs.studio.form_providers import materialize_form_schema
from silisocs.studio.form_schema import (
    PreviewContext,
    choice_items,
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
    run_choice_provider,
    run_preview_provider,
)
from silisocs.studio.routes.lookups import scenario_or_404
from silisocs.studio.routes.params import require_file_mapping
from silisocs.studio.routes.projections import choice_context
from silisocs.studio.state import studio_state

router = APIRouter()


@router.post("/api/compose")
async def api_compose(request: Request):
    state = studio_state(request)
    payload = await request.json()
    try:
        files = compose_files(dict(payload.get("files") or {}), dict(payload.get("updates") or {}))
        return {
            "files": files,
            "values": field_values(files),
            "schema": materialize_form_schema(
                files,
                defer_expensive=True,
                choice_context=choice_context(state, str(payload.get("source") or "workspace")),
            ),
        }
    except (ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/preflight")
async def api_preflight(request: Request):
    # Per call, not module scope: preflight reads its scale estimate off the real
    # turn/participation policies, so it is the one composer module that imports
    # the engine tree. Binding Studio must not pay for it.
    from silisocs.studio.preflight import preflight_payload  # noqa: PLC0415

    state = studio_state(request)
    payload = await request.json()
    files = payload.get("files")
    if files is None and payload.get("scenario"):
        loaded = scenario_or_404(
            state, str(payload["scenario"]), str(payload.get("source") or "workspace")
        )
        files = {name: item["text"] for name, item in loaded["files"].items()}
    if not isinstance(files, dict):
        raise HTTPException(status_code=422, detail="files or scenario is required")
    return preflight_payload(files)


@router.post("/api/form-preview")
async def api_form_preview(request: Request):
    workspace = studio_state(request).workspace
    payload = await request.json()
    files = payload.get("files") or {}
    provider = str(payload.get("provider") or "")
    item_key = str(payload.get("item_key") or "")
    require_file_mapping(files)
    try:
        return run_preview_provider(
            provider,
            files,
            item_key,
            PreviewContext(
                repository_root=workspace.source(str(payload.get("source") or "workspace")).path
            ),
        )
    except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/form-choices")
async def api_form_choices(request: Request):
    state = studio_state(request)
    payload = await request.json()
    files = require_file_mapping(payload.get("files") or {})
    provider = str(payload.get("provider") or "")
    try:
        context = choice_context(state, str(payload.get("source") or "workspace"))
        choices = await asyncio.to_thread(run_choice_provider, provider, files, context)
    except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"choices": choices, "items": choice_items(provider, choices, context)}


@router.get("/api/forms")
def api_forms():
    return {
        "items": [schema.to_dict() for schema in list_form_schemas()],
        "choice_providers": list(list_choice_providers()),
        "preview_providers": list(list_preview_providers()),
    }
