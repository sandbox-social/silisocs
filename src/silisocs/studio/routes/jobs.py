"""Job control plane: listing, stopping, launching, SSE streaming, run control."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.

import json

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from silisocs.studio.launch import ScenarioNotFoundError, prepare_launch
from silisocs.studio.routes.support import project_environment

router = APIRouter()


@router.get("/api/jobs")
def api_jobs(request: Request):
    return {"items": [job.to_dict() for job in request.app.state.jobs.store.list()]}


@router.get("/api/jobs/{job_id}")
def api_job(request: Request, job_id: str):
    try:
        return request.app.state.jobs.store.get(job_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.post("/api/jobs/{job_id}/stop")
def api_stop_job(request: Request, job_id: str):
    try:
        return request.app.state.jobs.stop(job_id).to_dict()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@router.get("/api/jobs/{job_id}/stream")
def api_job_stream(request: Request, job_id: str):
    jobs = request.app.state.jobs
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
    state = request.app.state
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
        return request.app.state.jobs.control(job_id, body if isinstance(body, dict) else {})
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
