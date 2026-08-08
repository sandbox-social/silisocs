"""Study definitions, study-scope analysis, and study launches."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.

import sys
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from silisocs.analysis.exploration import study_capability_document
from silisocs.analysis.panel import output_to_dict, serialize_controls
from silisocs.analysis.views import build_view, skip_reason
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.routes.support import (
    panel_or_404,
    panel_params,
    project_environment,
    resolve_run_links,
    study_or_404,
    study_scenario_names,
    study_view_or_404,
)
from silisocs.studio.studies import compose_study

router = APIRouter()


@router.get("/api/studies")
def api_studies(request: Request):
    return {"items": request.app.state.studies.list()}


@router.get("/api/studies/{study_id}")
def api_study(request: Request, study_id: str):
    return study_or_404(request.app.state, study_id)


@router.get("/api/studies/{study_id}/panels/{panel_name}")
def api_study_panel(request: Request, study_id: str, panel_name: str):
    # Mirror of the run-panel endpoint at study scope, so live surfaces
    # refresh ANY study panel through the shared renderPanel path instead of
    # rebuilding a specific panel's output shape by hand.
    state = request.app.state
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
    state = request.app.state
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


@router.get("/api/explore/studies/{study_id}/capabilities")
def api_explore_study_capabilities(request: Request, study_id: str):
    study = study_or_404(request.app.state, study_id, include_definition=False, include_board=False)
    return study_capability_document(load_study(study["path"]), study_id)


@router.get("/api/studies/{study_id}/notebook")
def api_study_notebook(request: Request, study_id: str):
    study = study_or_404(request.app.state, study_id, include_definition=False, include_board=False)
    path = Path(study["path"]) / "notebook.ipynb"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Study notebook not found")
    return Response(path.read_bytes(), media_type="application/x-ipynb+json")


@router.post("/api/studies/{study_id}")
async def api_save_study(request: Request, study_id: str):
    payload = await request.json()
    try:
        return request.app.state.studies.save(study_id, str(payload.get("yaml") or ""))
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/studies/{study_id}/compose")
async def api_compose_study(request: Request, study_id: str):
    try:
        request.app.state.studies.validate_id(study_id)
        payload = await request.json()
        return compose_study(
            str(payload.get("yaml") or ""),
            dict(payload.get("updates") or {}),
        )
    except (KeyError, ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/api/studies/{study_id}/launch")
async def api_launch_study(request: Request, study_id: str):
    state = request.app.state
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
