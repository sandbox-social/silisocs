"""Platform viewers: start one for a run (embedded or subprocess) and poll it."""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.

import socket

from fastapi import APIRouter, HTTPException, Request

from silisocs.studio.jobs import allocate_port
from silisocs.studio.routes.lookups import record_or_404
from silisocs.studio.state import studio_state
from silisocs.studio.viewers import (
    find_backend_dbs,
    viewer_app_factory,
    viewer_mount_path,
    visualizer_plan,
)

router = APIRouter()


def _port_is_serving(port: int, host: str = "127.0.0.1") -> bool:
    """Whether something is accepting connections on a loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((host, int(port))) == 0


@router.post("/api/viewers/{run_id:path}/{backend_type}")
def api_start_viewer(request: Request, run_id: str, backend_type: str):
    state = studio_state(request)
    record = record_or_404(state, run_id)
    # A viewer declaring an ASGI factory is mounted in-process and needs no
    # process, port, or readiness wait: it is ready the moment we answer.
    if viewer_app_factory(backend_type) is not None:
        return {"mode": "embedded", "url": viewer_mount_path(run_id, backend_type)}
    identity = f"{run_id}:{backend_type}"
    existing = next(
        (
            job
            for job in state.jobs.store.list()
            if job.kind == "viewer" and job.scenario == identity and job.status == "running"
        ),
        None,
    )
    if existing:
        return {
            **existing.to_dict(),
            "mode": "subprocess",
            "url": f"http://127.0.0.1:{existing.port}",
            "status_url": f"/api/viewers/{run_id}/{backend_type}/status",
        }
    match = next((item for item in find_backend_dbs(record.path) if item[0] == backend_type), None)
    if match is None:
        raise HTTPException(status_code=404, detail="Visualizer database not found")
    port = allocate_port()
    plan = visualizer_plan(match[0], match[1], port=port)
    if plan.missing_extra:
        raise HTTPException(
            status_code=409,
            detail=f'Install the "{plan.missing_extra}" extra to launch this viewer',
        )
    job = state.jobs.submit(
        kind="viewer",
        command=plan.cmd,
        cwd=state.repo_root,
        scenario=identity,
        snapshot={"run_id": run_id, "backend_type": backend_type, "database": str(match[1])},
        output_dir=record.path,
        port=port,
        env=plan.env,
    )
    return {
        **job.to_dict(),
        "mode": "subprocess",
        "url": plan.url,
        "status_url": f"/api/viewers/{run_id}/{backend_type}/status",
    }


@router.get("/api/viewers/{run_id:path}/{backend_type}/status")
def api_viewer_status(request: Request, run_id: str, backend_type: str):
    """Whether a subprocess viewer is actually serving yet.

    A launched process is not a bound port: the job goes 'running' at spawn,
    while the server only listens once its imports finish. Clients must wait
    on this, not on the job status.
    """
    state = studio_state(request)
    record_or_404(state, run_id)
    identity = f"{run_id}:{backend_type}"
    job = next(
        (
            item
            for item in state.jobs.store.list()
            if item.kind == "viewer" and item.scenario == identity
        ),
        None,
    )
    if job is None:
        return {"state": "absent", "detail": "No viewer has been started for this run."}
    if job.status in {"finished", "failed", "killed", "orphaned"}:
        code = job.exit_code
        detail = (
            f"Viewer process exited (code {code})."
            if code is not None
            else f"Viewer process {job.status}."
        )
        return {"state": "failed", "detail": detail, "status": job.status}
    if job.port and _port_is_serving(job.port):
        return {"state": "ready", "url": f"http://127.0.0.1:{job.port}"}
    return {"state": "starting", "status": job.status}
