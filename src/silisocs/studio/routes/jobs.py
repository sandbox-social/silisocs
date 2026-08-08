"""Job control plane: the live page, listing, stopping, launching, SSE, run control."""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

import json
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from silisocs.studio.launch import ScenarioNotFoundError, prepare_launch, project_environment
from silisocs.studio.routes.lookups import discover_all_runs
from silisocs.studio.state import studio_state

router = APIRouter()


@router.get("/live", response_class=HTMLResponse)
def live_page(request: Request, job: str | None = None):
    state = studio_state(request)
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


@router.get("/api/jobs")
def api_jobs(request: Request):
    return {"items": [job.to_dict() for job in studio_state(request).jobs.store.list()]}


@router.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    try:
        return studio_state(request).jobs.store.get(job_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.post("/api/jobs/{job_id}/stop")
def api_stop_job(request: Request, job_id: str):
    try:
        return studio_state(request).jobs.stop(job_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.get("/api/jobs/{job_id}/stream")
def api_job_stream(request: Request, job_id: str):
    jobs = studio_state(request).jobs
    try:
        jobs.store.get(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc

    def stream():
        for item in jobs.events(job_id):
            yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/api/launch")
async def api_launch(request: Request):
    state = studio_state(request)
    payload = await request.json()
    source_id = str(payload.pop("source", None) or "workspace")
    try:
        source = state.workspace.source(source_id)
        spec = prepare_launch(
            payload,
            repository_root=state.repo_root,
            output_root=state.output_root,
            draft_root=state.studio_state / "launch_configs",
        )
    except (KeyError, ScenarioNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = state.jobs.submit(
        kind="run",
        command=spec.command,
        cwd=source.path,
        scenario=spec.scenario,
        snapshot=spec.snapshot,
        output_dir=spec.output_dir,
        env=project_environment(state, source_id),
        control_path=spec.control_path,
    )
    return job.to_dict()


@router.post("/api/jobs/{job_id}/control")
async def api_job_control(request: Request, job_id: str):
    try:
        body = await request.json()
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid JSON body") from exc
    try:
        return studio_state(request).jobs.control(job_id, body if isinstance(body, dict) else {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
