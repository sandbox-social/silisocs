"""HTML pages (and the cached static assets they load)."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from silisocs.analysis.exploration import (
    ExplorationState,
    run_capability_document,
    study_capability_document,
)
from silisocs.analysis.panel import list_panels
from silisocs.analysis.views import BUILTIN_VIEWS, build_view
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.catalog import arrange_runs
from silisocs.studio.forms import (
    field_values,
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
    materialize_form_schema,
)
from silisocs.studio.routes.support import (
    choice_context,
    compare_run_ids,
    discover_all_runs,
    panel_param_overrides,
    record_or_404,
    resolve_run_links,
    scenario_or_404,
    study_composer_catalog,
    study_or_404,
    study_view_or_404,
)

router = APIRouter()


@router.get("/assets/{name}")
def asset(request: Request, name: str):
    cached = request.app.state.assets.get(name)
    if cached is None:
        raise HTTPException(status_code=404, detail=f"Unknown asset {name!r}")
    status, body, headers = cached.response(request.headers.get("if-none-match"))
    if status == 304:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type=cached.media_type, headers=headers)


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    state = request.app.state
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
    state = request.app.state
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


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, q: str = "", status: str = "", sort: str = "recent"):
    state = request.app.state
    records = discover_all_runs(state)
    statuses = sorted({record.artifact.status or "unknown" for record in records})
    return state.templates.TemplateResponse(
        request,
        "runs.html",
        {
            "runs": arrange_runs(records, query=q, status=status, sort=sort),
            "statuses": statuses,
            "filters": {"q": q, "status": status, "sort": sort},
            "active": "runs",
        },
    )


@router.get("/live", response_class=HTMLResponse)
def live_page(request: Request, job: str | None = None):
    state = request.app.state
    selected = None
    if job:
        try:
            selected = state.jobs.store.get(job)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
    all_jobs = state.jobs.store.list()
    run_id = None
    if selected and selected.output_dir:
        match = next(
            (
                record
                for record in discover_all_runs(state)
                if record.path.resolve() == Path(selected.output_dir).resolve()
            ),
            None,
        )
        run_id = match.id if match else None
    interactive = bool(selected and selected.to_dict().get("interactive"))
    return state.templates.TemplateResponse(
        request,
        "live.html",
        {
            "jobs": all_jobs,
            "job": selected,
            "run_id": run_id,
            "interactive": interactive,
            "active": "live",
        },
    )


@router.get("/studies", response_class=HTMLResponse)
def studies_page(request: Request):
    state = request.app.state
    return state.templates.TemplateResponse(
        request,
        "studies.html",
        {"studies": state.studies.list(), "active": "studies"},
    )


@router.get("/studies/new", response_class=HTMLResponse)
def new_study_page(request: Request, name: str = "new_study"):
    state = request.app.state
    studies = state.studies
    try:
        study_id = studies.validate_id(name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    scenario_choices = state.workspace.scenarios()
    definition = studies.new_definition(
        study_id,
        scenario=scenario_choices[0] if scenario_choices else None,
        working_directory=str(state.repo_root),
    )
    study = {
        "id": study_id,
        "name": study_id,
        "question": "",
        "path": str(studies.root / study_id),
        "definition": definition,
        "yaml": yaml.safe_dump(definition, sort_keys=False),
        "board": [],
    }
    return state.templates.TemplateResponse(
        request,
        "study.html",
        {
            "study": study,
            "view": None,
            "tab": "definition",
            "job": None,
            "active": "studies",
            "notebook_exists": False,
            **study_composer_catalog(state),
        },
    )


@router.get("/studies/{study_id}", response_class=HTMLResponse)
def study_page(request: Request, study_id: str, view: str = "progress", tab: str = "board"):
    state = request.app.state
    study = study_or_404(state, study_id)
    selected_view = {
        "board": "progress",
        "compare": "comparison",
        "hypotheses": "hypotheses",
    }.get(tab, view)
    # Validate through the same allowlist the run routes use: an unmapped tab
    # falls through to the raw ?view= param, which build_view would otherwise
    # turn into a Path.read_text + class_path import (arbitrary file read +
    # import-time code execution). A study resolves built-in study views plus
    # study-scope views shipped by its declared scenarios. ``p.<panel>.
    # <param>`` query args drive panel params exactly as on the run page, so
    # study-panel controls and links work symmetrically.
    built = (
        resolve_run_links(
            state,
            build_view(
                study_view_or_404(state, selected_view, study),
                load_study(study["path"]),
                panel_param_overrides(request.query_params),
            ),
        )
        if tab != "definition"
        else None
    )
    related = next((job for job in state.jobs.store.list() if job.parent_study == study_id), None)
    return state.templates.TemplateResponse(
        request,
        "study.html",
        {
            "study": study,
            "view": built,
            "tab": tab,
            "job": related,
            "active": "studies",
            "notebook_exists": (Path(study["path"]) / "notebook.ipynb").is_file(),
            **(study_composer_catalog(state) if tab == "definition" else {}),
        },
    )


@router.get("/explore/study/{study_id}", response_class=HTMLResponse)
def explore_study_page(request: Request, study_id: str):
    state = request.app.state
    study = study_or_404(state, study_id)
    try:
        exploration = ExplorationState.from_query("study", (study_id,), request.query_params)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    artifact = load_study(study["path"])
    return state.templates.TemplateResponse(
        request,
        "explore_study.html",
        {
            "study": study,
            "state": exploration.to_dict(),
            "capabilities": study_capability_document(artifact, study_id),
            "active": "studies",
        },
    )


@router.get("/scenarios", response_class=HTMLResponse)
def scenarios_page(request: Request):
    state = request.app.state
    return state.templates.TemplateResponse(
        request,
        "scenarios.html",
        {"scenarios": state.workspace.scenarios(), "active": "scenarios"},
    )


@router.get("/scenarios/new", response_class=HTMLResponse)
def new_scenario_page(request: Request, name: str = "new_scenario"):
    state = request.app.state
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
    state = request.app.state
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


@router.get("/explore/run/{run_id:path}", response_class=HTMLResponse)
def explore_run_page(request: Request, run_id: str):
    state = request.app.state
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
    state = request.app.state
    # The template guards on <2 records; parsing is shared with the API route.
    records = [record_or_404(state, run_id) for run_id in compare_run_ids(request)]
    return state.templates.TemplateResponse(
        request,
        "explore_compare.html",
        {"records": records, "run_ids": [record.id for record in records], "active": "runs"},
    )
