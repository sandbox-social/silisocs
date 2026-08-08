"""Scenario documents, workspace repositories, and the composer form APIs."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.

import asyncio

import yaml
from fastapi import APIRouter, HTTPException, Request

from silisocs.studio.catalog import discover_runs
from silisocs.studio.forms import (
    PreviewContext,
    choice_items,
    compose_files,
    field_values,
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
    materialize_form_schema,
    preflight_payload,
    run_choice_provider,
    run_preview_provider,
)
from silisocs.studio.routes.support import (
    choice_context,
    require_file_mapping,
    run_json,
    scenario_or_404,
)

router = APIRouter()


@router.get("/api/scenarios")
def api_scenarios(request: Request):
    return {"items": request.app.state.workspace.scenarios()}


@router.get("/api/repositories")
def api_repositories(request: Request):
    workspace = request.app.state.workspace
    return {
        "items": [source.to_dict() for source in workspace.sources],
        "extensions": workspace.extension_catalog(),
        "discovery_errors": workspace.discovery_errors,
    }


@router.post("/api/repositories")
async def api_add_repository(request: Request):
    workspace = request.app.state.workspace
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
        source = request.app.state.workspace.rename(source_id, str(payload.get("nickname") or ""))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"source": source.to_dict()}


@router.post("/api/repositories/refresh")
def api_refresh_repositories(request: Request):
    workspace = request.app.state.workspace
    workspace.refresh_extensions()
    return {
        "extensions": workspace.extension_catalog(),
        "discovery_errors": workspace.discovery_errors,
    }


@router.delete("/api/repositories/{source_id}")
def api_remove_repository(request: Request, source_id: str):
    try:
        request.app.state.workspace.remove(source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"removed": source_id}


@router.get("/api/scenarios/{scenario_name}")
def api_scenario(request: Request, scenario_name: str, source: str = "workspace"):
    state = request.app.state
    scenario = scenario_or_404(state, scenario_name, source)
    files = {name: item["text"] for name, item in scenario["files"].items()}
    return {
        **scenario,
        "source": source,
        "schema": materialize_form_schema(
            files,
            defer_expensive=True,
            choice_context=choice_context(state, source),
        ),
        "values": field_values(files),
    }


@router.get("/api/scenarios/{scenario_name}/runs")
def api_scenario_runs(request: Request, scenario_name: str, source: str = "workspace"):
    state = request.app.state
    scenario_or_404(state, scenario_name, source)
    return {
        "items": [
            run_json(record)
            for record in discover_runs(state.output_root, subtree=scenario_name)
            if record.artifact.scenario == scenario_name
        ]
    }


@router.post("/api/scenarios")
async def api_save_scenario(request: Request):
    payload = await request.json()
    name = str(payload.get("name") or "")
    source = str(payload.get("source") or "workspace")
    files = require_file_mapping(payload.get("files") or {})
    try:
        return request.app.state.workspace.scenario_repository(source).save(name, files)
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/compose")
async def api_compose(request: Request):
    state = request.app.state
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
    state = request.app.state
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
    workspace = request.app.state.workspace
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
    state = request.app.state
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
