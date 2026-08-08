"""Connected workspace repositories: listing, adding, renaming, and removing them."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

from fastapi import APIRouter, HTTPException, Request

from silisocs.studio.state import studio_state

router = APIRouter()


@router.get("/api/repositories")
def api_repositories(request: Request):
    workspace = studio_state(request).workspace
    return {
        "items": [source.to_dict() for source in workspace.sources],
        "extensions": workspace.extension_catalog(),
        "discovery_errors": workspace.discovery_errors,
    }


@router.post("/api/repositories")
async def api_add_repository(request: Request):
    workspace = studio_state(request).workspace
    payload = await request.json()
    try:
        source = workspace.add(
            str(payload.get("path") or ""),
            nickname=str(payload.get("nickname") or ""),
        )
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "source": source.to_dict(),
        "scenarios": workspace.scenario_repository(source.id).list(),
        "extensions": workspace.extension_catalog(),
    }


@router.patch("/api/repositories/{source_id}")
async def api_rename_repository(request: Request, source_id: str):
    payload = await request.json()
    try:
        source = studio_state(request).workspace.rename(
            source_id, str(payload.get("nickname") or "")
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"source": source.to_dict()}


@router.post("/api/repositories/refresh")
def api_refresh_repositories(request: Request):
    workspace = studio_state(request).workspace
    workspace.refresh_extensions()
    return {
        "extensions": workspace.extension_catalog(),
        "discovery_errors": workspace.discovery_errors,
    }


@router.delete("/api/repositories/{source_id}")
def api_remove_repository(request: Request, source_id: str):
    try:
        studio_state(request).workspace.remove(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"removed": source_id}
