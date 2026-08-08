"""Run catalog and raw run event streams.

Registered LAST in ``create_app``: ``/api/runs/{run_id:path}`` is a greedy
catch-all, so it would shadow every suffix route (``/views/…``, ``/panels/…``,
``/report``) if it were registered ahead of them.
"""
# ruff: noqa: D103
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from silisocs.studio.catalog import arrange_runs
from silisocs.studio.routes.support import discover_all_runs, record_or_404, run_json

router = APIRouter()


@router.get("/api/runs")
def api_runs(request: Request, q: str = "", status: str = "", sort: str = "recent"):
    records = arrange_runs(discover_all_runs(request.app.state), query=q, status=status, sort=sort)
    return {"items": [run_json(record) for record in records]}


@router.get("/api/runs/{run_id:path}/events/{stream}")
def api_run_events(
    request: Request, run_id: str, stream: str, since_index: int = 0, limit: int = 500
):
    record = record_or_404(request.app.state, run_id)
    readers = {
        "action": record.artifact.iter_actions,
        "exposure": record.artifact.iter_exposures,
        "probe": record.artifact.iter_probes,
        "harness": record.artifact.iter_harness_events,
    }
    if stream not in readers:
        raise HTTPException(status_code=404, detail=f"Unknown event stream {stream!r}")
    start = max(0, since_index)
    page_size = max(1, min(limit, 5000))
    rows: list[dict[str, Any]] = []
    for index, row in enumerate(readers[stream]()):
        if index < start:
            continue
        if len(rows) >= page_size:
            break
        rows.append(row)
    return {"items": rows, "since_index": start, "next_index": start + len(rows)}


@router.get("/api/runs/{run_id:path}")
def api_run(request: Request, run_id: str):
    return run_json(record_or_404(request.app.state, run_id))
