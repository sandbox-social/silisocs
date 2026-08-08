"""Study definitions: the pages, the study-scope analysis API, and study launches."""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from silisocs.analysis.exploration import ExplorationState, study_capability_document
from silisocs.analysis.panel import output_to_dict, serialize_controls
from silisocs.analysis.views import build_view, skip_reason
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.launch import project_environment
from silisocs.studio.routes.lookups import (
    panel_or_404,
    study_or_404,
    study_scenario_names,
    study_view_or_404,
)
from silisocs.studio.routes.params import panel_param_overrides, panel_params
from silisocs.studio.routes.projections import resolve_run_links, study_composer_catalog
from silisocs.studio.save_conflicts import NEW_DOCUMENT, SaveConflictError
from silisocs.studio.state import studio_state
from silisocs.studio.studies import compose_study

router = APIRouter()


@router.get("/studies", response_class=HTMLResponse)
def studies_page(request: Request):
    state = studio_state(request)
    return state.templates.TemplateResponse(
        request,
        "studies.html",
        {"studies": state.studies.list(), "active": "studies"},
    )


@router.get("/studies/new", response_class=HTMLResponse)
def new_study_page(request: Request, name: str = "new_study"):
    state = studio_state(request)
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
        # Creating: the save conflicts if the study meanwhile came to exist.
        "fingerprint": NEW_DOCUMENT,
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
    state = studio_state(request)
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
    state = studio_state(request)
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


@router.get("/api/studies")
def api_studies(request: Request):
    return {"items": studio_state(request).studies.list()}


@router.get("/api/studies/{study_id}")
def api_study(request: Request, study_id: str):
    return study_or_404(studio_state(request), study_id)


@router.get("/api/studies/{study_id}/panels/{panel_name}")
def api_study_panel(request: Request, study_id: str, panel_name: str):
    # Mirror of the run-panel endpoint at study scope, so live surfaces
    # refresh ANY study panel through the shared renderPanel path instead of
    # rebuilding a specific panel's output shape by hand.
    state = studio_state(request)
    study = study_or_404(state, study_id, include_board=False)
    panel = panel_or_404(state, panel_name, "study", study_scenario_names(study))
    artifact = load_study(study["path"])
    reason = skip_reason(panel, artifact)
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
            "output": output_to_dict(panel().build(artifact, params)),
        },
    )


@router.get("/api/studies/{study_id}/compare")
def api_study_compare(
    request: Request,
    study_id: str,
    compare: str | None = None,
    baseline: str | None = None,
    hypothesis: str | None = None,
    panel: str = "condition_comparison",
    view: str = "comparison",
):
    # `panel`/`view` let a study ship its own comparison panel and still be
    # driven by the same query params, instead of hard-wiring the built-in.
    state = studio_state(request)
    study = study_or_404(state, study_id)
    compare_view = study_view_or_404(state, view, study)
    params = {
        key: value
        for key, value in (
            ("compare", compare),
            ("baseline", baseline),
            ("hypothesis", hypothesis),
        )
        if value
    }
    return resolve_run_links(
        state,
        build_view(
            compare_view,
            load_study(study["path"]),
            param_overrides={panel: params} if params else None,
        ),
    )


@router.get("/api/studies/{study_id}/notebook")
def api_study_notebook(request: Request, study_id: str):
    study = study_or_404(
        studio_state(request), study_id, include_definition=False, include_board=False
    )
    path = Path(study["path"]) / "notebook.ipynb"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Study notebook not found")
    return Response(path.read_bytes(), media_type="application/x-ipynb+json")


@router.post("/api/studies/{study_id}")
async def api_save_study(request: Request, study_id: str):
    """Write study.yaml verbatim, refusing a save built on stale bytes.

    ``fingerprint`` (and the optional ``baseline`` the conflict diff reads) are
    opt-in: a payload without them overwrites, as it always did.
    """
    payload = await request.json()
    baseline = payload.get("baseline")
    try:
        return studio_state(request).studies.save(
            study_id,
            str(payload.get("yaml") or ""),
            fingerprint=str(payload.get("fingerprint") or ""),
            baseline=baseline if isinstance(baseline, str) else None,
        )
    except SaveConflictError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/studies/{study_id}/compose")
async def api_compose_study(request: Request, study_id: str):
    try:
        studio_state(request).studies.validate_id(study_id)
        payload = await request.json()
        return compose_study(
            str(payload.get("yaml") or ""),
            dict(payload.get("updates") or {}),
        )
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/studies/{study_id}/launch")
async def api_launch_study(request: Request, study_id: str):
    state = studio_state(request)
    study = study_or_404(state, study_id)
    payload = await request.json()
    concurrency = max(1, int(payload.get("max_concurrent", 1) or 1))
    command = [
        sys.executable,
        "-m",
        "silisocs.studies.run_study",
        "--study",
        study["definition_path"],
        "--repo-root",
        str(state.repo_root),
        "run",
        "--max-concurrent",
        str(concurrency),
        "--yes",
    ]
    job = state.jobs.submit(
        kind="study_run",
        command=command,
        cwd=state.repo_root,
        scenario=study_id,
        snapshot={"study": study["definition"], "command": command},
        output_dir=study["path"],
        parent_study=study_id,
        env=project_environment(state),
    )
    return job.to_dict()
