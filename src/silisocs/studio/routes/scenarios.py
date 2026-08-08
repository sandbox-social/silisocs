"""Scenario documents: the editor pages and the API behind them."""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from silisocs.studio.catalog import discover_runs
from silisocs.studio.compose import field_values
from silisocs.studio.form_providers import materialize_form_schema
from silisocs.studio.routes.lookups import scenario_or_404
from silisocs.studio.routes.params import require_file_mapping
from silisocs.studio.routes.projections import choice_context, run_json
from silisocs.studio.save_conflicts import NEW_DOCUMENT, SaveConflictError
from silisocs.studio.state import studio_state

router = APIRouter()


def _string_mapping(value: Any, detail: str) -> dict[str, str]:
    """Return an optional ``{name: text}`` payload field, or raise 422."""
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise HTTPException(status_code=422, detail=detail)
    return value


@router.get("/scenarios", response_class=HTMLResponse)
def scenarios_page(request: Request):
    state = studio_state(request)
    return state.templates.TemplateResponse(
        request,
        "scenarios.html",
        {"scenarios": state.workspace.scenarios(), "active": "scenarios"},
    )


@router.get("/scenarios/new", response_class=HTMLResponse)
def new_scenario_page(request: Request, name: str = "new_scenario"):
    state = studio_state(request)
    scenarios = state.scenarios
    try:
        safe_name = scenarios.validate_name(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    files = scenarios.default_files(safe_name)
    scenario = {
        "name": safe_name,
        "path": str(scenarios.root / safe_name),
        "source": "workspace",
        "source_label": "Workspace",
        "files": {
            key: {"text": value, "data": yaml.safe_load(value)} for key, value in files.items()
        },
        # Creating: each document's save conflicts if the file meanwhile appeared.
        "fingerprints": dict.fromkeys(files, NEW_DOCUMENT),
    }
    return state.templates.TemplateResponse(
        request,
        "scenario.html",
        {
            "scenario": scenario,
            "schema": materialize_form_schema(
                files,
                defer_expensive=True,
                choice_context=choice_context(state),
            ),
            "values": field_values(files),
            "panel_catalog": [],
            "history": [],
            "active": "scenarios",
        },
    )


@router.get("/scenarios/{scenario_name}", response_class=HTMLResponse)
def scenario_page(request: Request, scenario_name: str, source: str = "workspace"):
    state = studio_state(request)
    scenario = scenario_or_404(state, scenario_name, source)
    scenario["source"] = source
    scenario["source_label"] = state.workspace.source(source).label
    text_files = {name: item["text"] for name, item in scenario["files"].items()}
    return state.templates.TemplateResponse(
        request,
        "scenario.html",
        {
            "scenario": scenario,
            "schema": materialize_form_schema(
                text_files,
                defer_expensive=True,
                choice_context=choice_context(state, source),
            ),
            "values": field_values(text_files),
            "panel_catalog": [],
            "active": "scenarios",
        },
    )


@router.get("/api/scenarios")
def api_scenarios(request: Request):
    return {"items": studio_state(request).workspace.scenarios()}


@router.get("/api/scenarios/{scenario_name}")
def api_scenario(request: Request, scenario_name: str, source: str = "workspace"):
    state = studio_state(request)
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
    state = studio_state(request)
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
    """Write a scenario's documents, refusing a save built on stale bytes.

    ``fingerprints`` (and the optional ``baselines`` the conflict diff reads)
    are opt-in: a payload without them overwrites, as it always did.
    """
    payload = await request.json()
    name = str(payload.get("name") or "")
    source = str(payload.get("source") or "workspace")
    files = require_file_mapping(payload.get("files") or {})
    fingerprints = _string_mapping(
        payload.get("fingerprints") or {},
        "fingerprints must map relative names to the fingerprint the edit was based on",
    )
    baselines = _string_mapping(
        payload.get("baselines") or {},
        "baselines must map relative names to the YAML text the edit was based on",
    )
    try:
        return (
            studio_state(request)
            .workspace.scenario_repository(source)
            .save(name, files, fingerprints=fingerprints, baselines=baselines)
        )
    except SaveConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
