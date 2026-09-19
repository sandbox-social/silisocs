"""Job control plane: the live page, listing, stopping, launching, SSE, run control."""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

import json
import secrets
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from silisocs.studio.launch import ScenarioNotFoundError, prepare_launch, project_environment
from silisocs.studio.routes.lookups import discover_all_runs, record_or_404
from silisocs.studio.state import studio_state

router = APIRouter()


def _branch_body(body: Any) -> int | None:
    if not isinstance(body, dict):
        raise ValueError("Branch request must be a JSON object")
    checkpoint_step = body.get("checkpoint_step")
    if checkpoint_step is not None and (
        isinstance(checkpoint_step, bool) or not isinstance(checkpoint_step, int)
    ):
        raise ValueError("checkpoint_step must be an integer or null")
    return checkpoint_step


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


@router.post("/api/runs/{run_id:path}/branches/plan")
async def api_plan_branch(request: Request, run_id: str):
    """Resolve a branch request without creating output or queueing a process."""
    from silisocs.runtime.execution.branching import plan_checkpoint_branch  # noqa: PLC0415
    from silisocs.studio.branches import find_parent_job  # noqa: PLC0415

    state = studio_state(request)
    record = record_or_404(state, run_id)
    try:
        body = await request.json()
        checkpoint_step = _branch_body(body)
        find_parent_job(state.jobs, record.path)
        overrides = body.get("overrides") or []
        if not isinstance(overrides, list) or not all(isinstance(item, str) for item in overrides):
            raise ValueError("overrides must be a list of strings")
        mode = str(body.get("mode") or "exact").strip().lower()
        seed = body.get("continuation_seed")
        if mode == "resample" and seed is None:
            seed = secrets.randbelow(2**31 - 1) + 1
        plan = plan_checkpoint_branch(
            record.path,
            checkpoint_step=checkpoint_step,
            mode=mode,
            continuation_seed=seed,
            overrides=tuple(overrides),
        )
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "planned": True,
        "runtime_validation": "on_launch",
        "parent_run_id": run_id,
        **plan.to_dict(),
    }


@router.post("/api/runs/{run_id:path}/branches")
async def api_create_branches(request: Request, run_id: str):
    """Plan a batch, then queue each child as an ordinary run."""
    from silisocs.studio.branches import (  # noqa: PLC0415
        find_parent_job,
        prepare_branches,
        submit_prepared_branches,
    )

    state = studio_state(request)
    record = record_or_404(state, run_id)
    try:
        body = await request.json()
        checkpoint_step = _branch_body(body)
        interactive = body.get("interactive", True)
        start_paused = body.get("start_paused", True)
        if not isinstance(interactive, bool) or not isinstance(start_paused, bool):
            raise ValueError("interactive and start_paused must be booleans")
        branches = body.get("branches")
        if branches is None:
            branches = [
                {
                    "name": body.get("name"),
                    "mode": body.get("mode"),
                    "continuation_seed": body.get("continuation_seed"),
                    "overrides": body.get("overrides"),
                }
            ]
        if not isinstance(branches, list) or not all(isinstance(item, dict) for item in branches):
            raise ValueError("branches must be a list of objects")
        parent_job = find_parent_job(state.jobs, record.path)
        group_id, prepared, cwd, env = prepare_branches(
            parent_job=parent_job,
            parent_run_id=run_id,
            parent_run_path=record.path,
            output_root=state.output_root,
            requests=branches,
            checkpoint_step=checkpoint_step,
            interactive=interactive,
            start_paused=start_paused,
        )
        jobs = submit_prepared_branches(
            state.jobs,
            prepared,
            cwd=cwd,
            env=env,
            scenario=parent_job.scenario,
        )
    except (ValueError, FileNotFoundError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "group_id": group_id,
        "parent_run_id": run_id,
        "items": [
            {**job.to_dict(), "branch_id": item.id, "name": item.name, **item.plan.to_dict()}
            for job, item in zip(jobs, prepared, strict=True)
        ],
    }


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
