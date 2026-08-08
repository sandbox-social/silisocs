"""Exploration and analysis: the explore pages, queries, views, panels, and reports."""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from silisocs.analysis.exploration import (
    ExplorationQuery,
    ExplorationState,
    compare_runs,
    query_entities,
    query_events,
    query_evidence,
    query_relationships,
    query_series,
    run_capability_document,
    run_story,
    study_capability_document,
)
from silisocs.analysis.panel import list_panels, output_to_dict, serialize_controls
from silisocs.analysis.report import render_report
from silisocs.analysis.views import BUILTIN_VIEWS, build_view, skip_reason
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.routes.lookups import (
    panel_or_404,
    record_or_404,
    study_or_404,
    view_or_404,
)
from silisocs.studio.routes.params import (
    compare_run_ids,
    panel_param_overrides,
    panel_params,
)
from silisocs.studio.routes.projections import resolve_run_links
from silisocs.studio.state import studio_state

router = APIRouter()


def exploration_query(request: Request) -> ExplorationQuery:
    try:
        return ExplorationQuery.from_mapping(request.query_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/explore/run/{run_id:path}", response_class=HTMLResponse)
def explore_run_page(request: Request, run_id: str):
    state = studio_state(request)
    record = record_or_404(state, run_id)
    try:
        exploration = ExplorationState.from_query("run", (run_id,), request.query_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    capabilities = run_capability_document(record.artifact, run_id)
    return state.templates.TemplateResponse(
        request,
        "explore.html",
        {
            "record": record,
            "state": exploration.to_dict(),
            "capabilities": capabilities,
            "active": "runs",
        },
    )


@router.get("/explore/compare", response_class=HTMLResponse)
def explore_compare_page(request: Request):
    state = studio_state(request)
    # The template guards on <2 records; parsing is shared with the API route.
    records = [record_or_404(state, run_id) for run_id in compare_run_ids(request)]
    return state.templates.TemplateResponse(
        request,
        "explore_compare.html",
        {"records": records, "run_ids": [record.id for record in records], "active": "runs"},
    )


@router.get("/api/explore/runs/{run_id:path}/capabilities")
def api_explore_run_capabilities(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return run_capability_document(record.artifact, run_id)


@router.get("/api/explore/runs/{run_id:path}/events")
def api_explore_run_events(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return query_events(record.artifact, exploration_query(request))


@router.get("/api/explore/runs/{run_id:path}/entities")
def api_explore_run_entities(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return query_entities(record.artifact, exploration_query(request))


@router.get("/api/explore/runs/{run_id:path}/series")
def api_explore_run_series(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return query_series(record.artifact, exploration_query(request))


@router.get("/api/explore/runs/{run_id:path}/relationships")
def api_explore_run_relationships(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return query_relationships(record.artifact, exploration_query(request))


@router.get("/api/explore/runs/{run_id:path}/evidence")
def api_explore_run_evidence(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    params = request.query_params
    entity = (params.get("entity") or "").strip() or None
    episode_raw = (params.get("episode") or "").strip()
    try:
        episode = int(episode_raw) if episode_raw else None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="episode must be an integer") from exc
    return query_evidence(
        record.artifact, exploration_query(request), entity=entity, episode=episode
    )


@router.get("/api/explore/runs/{run_id:path}/story")
def api_explore_run_story(request: Request, run_id: str):
    record = record_or_404(studio_state(request), run_id)
    return run_story(record.artifact)


@router.get("/api/explore/studies/{study_id}/capabilities")
def api_explore_study_capabilities(request: Request, study_id: str):
    study = study_or_404(
        studio_state(request), study_id, include_definition=False, include_board=False
    )
    return study_capability_document(load_study(study["path"]), study_id)


@router.get("/api/explore/compare")
def api_explore_compare(request: Request):
    state = studio_state(request)
    run_ids = compare_run_ids(request)
    if len(run_ids) < 2:
        raise HTTPException(status_code=422, detail="Provide at least two runs to compare")
    artifacts = [(run_id, record_or_404(state, run_id).artifact) for run_id in run_ids]
    return compare_runs(artifacts, exploration_query(request))


@router.get("/api/runs/{run_id:path}/views/{view_name}")
def api_run_view(request: Request, run_id: str, view_name: str):
    state = studio_state(request)
    record = record_or_404(state, run_id)
    try:
        return resolve_run_links(
            state,
            build_view(
                view_or_404(state, view_name, record.artifact.scenario),
                record.artifact,
                panel_param_overrides(request.query_params),
            ),
        )
    except (KeyError, ValueError) as exc:  # unknown panel in a shipped view
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/runs/{run_id:path}/panels/{panel_name}")
def api_run_panel(request: Request, run_id: str, panel_name: str):
    state = studio_state(request)
    record = record_or_404(state, run_id)
    scenario_names = [record.artifact.scenario] if record.artifact.scenario else []
    panel = panel_or_404(state, panel_name, "run", scenario_names)
    reason = skip_reason(panel, record.artifact)
    if reason:
        raise HTTPException(status_code=409, detail=f"Panel {panel_name!r} {reason}")
    params = panel_params(request.query_params)
    return resolve_run_links(
        state,
        {
            "name": panel.name,
            "title": panel.title,
            "params": params,
            "controls": serialize_controls(panel, params),
            "output": output_to_dict(panel().build(record.artifact, params)),
        },
    )


@router.get("/api/runs/{run_id:path}/report")
def api_run_report(request: Request, run_id: str, view: str = "overview"):
    state = studio_state(request)
    record = record_or_404(state, run_id)
    report_view = view_or_404(state, view, record.artifact.scenario)
    if report_view.scope != "run":
        raise HTTPException(status_code=404, detail=f"Unknown report view {view!r}")
    document = render_report(record.path, report_view)
    filename = f"{(record.artifact.scenario or 'run')}-{view}.html"
    return Response(
        document,
        media_type="text/html",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/panels")
def api_panels():
    return {
        "items": [
            {
                "name": panel.name,
                "title": panel.title,
                "scope": panel.scope,
                "requires": sorted(panel.requires),
            }
            for panel in list_panels()
        ]
    }


@router.get("/api/views")
def api_views():
    return {"items": [{"name": name, **spec} for name, spec in BUILTIN_VIEWS.items()]}
