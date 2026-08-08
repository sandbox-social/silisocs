"""The run catalog, the run detail page, and the raw run event streams.

Registered LAST in ``create_app``: ``/api/runs/{run_id:path}`` is a greedy
catch-all, so it would shadow every suffix route (``/views/…``, ``/panels/…``,
``/report``) if it were registered ahead of them.
"""
#
# D103: a route handler's contract is its decorator (method + path) and its
# return shape; the ones with a non-obvious rule carry a docstring.
#
# NOTE: no `from __future__ import annotations` in the route modules. FastAPI
# resolves handler annotations at registration time; keeping them real objects
# is the contract the whole router surface relies on.

import shlex
from difflib import unified_diff
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from silisocs.analysis.views import build_view
from silisocs.studio.catalog import arrange_runs
from silisocs.studio.routes.lookups import (
    discover_all_runs,
    record_or_404,
    run_view_names,
    view_or_404,
)
from silisocs.studio.routes.params import panel_param_overrides
from silisocs.studio.routes.projections import resolve_run_links, run_json
from silisocs.studio.scenario_repository import ScenarioRepository
from silisocs.studio.state import studio_state
from silisocs.studio.viewers import find_backend_dbs

router = APIRouter()


def _run_facets(artifact: Any) -> dict[str, Any]:
    """Episode range and agent list a run's shell controls draw choices from."""
    max_episode = -1
    action_count = 0
    agents: set[str] = set()
    probes: set[str] = set()
    for row in artifact.actions:
        action_count += 1
        episode = row.get("episode")
        if isinstance(episode, int):
            max_episode = max(max_episode, episode)
        source = row.get("source_user")
        if source:
            agents.add(str(source))
    for row in artifact.probes:
        episode = row.get("episode")
        if isinstance(episode, int):
            max_episode = max(max_episode, episode)
        if row.get("label"):
            probes.add(str(row["label"]))
    from silisocs.analysis.panels._shared import backend_types  # noqa: PLC0415

    return {
        "max_episode": max_episode,
        "action_count": action_count,
        "agents": sorted(agents),
        "probes": sorted(probes),
        # Manifest-declared backend types; backend_select controls render only
        # when a run actually has more than one.
        "backends": backend_types(artifact),
    }


def _watch_snapshot(artifact: Any, job: Any, facets: dict[str, Any]) -> dict[str, Any]:
    """Build backend-neutral initial Watch counters from persisted run artifacts."""
    status = str(artifact.status or job.status)
    total_steps = artifact.num_steps
    latest_episode = int(facets.get("max_episode", -1))
    if status == "success" and total_steps is not None:
        step = f"Episode {total_steps}/{total_steps} complete"
    elif latest_episode >= 0:
        current = latest_episode + 1
        step = (
            f"Episode {current}/{total_steps} running"
            if total_steps
            else f"Episode {current} running"
        )
    else:
        step = "Waiting for events"

    started = job.started_at or job.created_at
    ended = job.ended_at or started
    elapsed = max(0, int(ended - started))
    usage = artifact.llm_usage or {}
    totals = usage.get("totals", {}) if isinstance(usage, dict) else {}
    tokens = totals.get("total_tokens") if isinstance(totals, dict) else None
    cost = usage.get("estimated_cost_usd") if isinstance(usage, dict) else None
    usage_label = (
        f"${float(cost):.4f} estimated"
        if cost is not None
        else f"{int(tokens):,} tokens"
        if tokens is not None
        else "Usage pending"
    )
    action_count = int(facets.get("action_count", 0))
    return {
        "status": status,
        "step": step,
        "elapsed": f"{elapsed // 60}:{elapsed % 60:02d} elapsed",
        "actions": f"{action_count:,} {'action' if action_count == 1 else 'actions'}",
        "usage": usage_label,
    }


def _effective_config_text(run_dir: Path) -> str:
    candidates = [
        run_dir / "effective_config.yaml",
        *run_dir.glob("configs/**/effective_config.yaml"),
    ]
    candidate = next((path for path in candidates if path.is_file()), None)
    return candidate.read_text(encoding="utf-8") if candidate else ""


def _merge_mapping(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Recursively merge YAML mappings while preserving their source order."""
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_mapping(target[key], value)
        else:
            target[key] = value


def _scenario_baseline_text(scenarios: ScenarioRepository, scenario_name: str | None) -> str:
    """Project a scenario's authored config groups into one comparable document."""
    if not scenario_name:
        return ""
    try:
        scenario = scenarios.load(scenario_name)
    except (KeyError, ValueError):
        return ""

    baseline: dict[str, Any] = {}
    packages = {
        "world/default.yaml": None,
        "agents/default.yaml": "agents",
        "sim.yaml": "sim",
        "env.yaml": "env",
        "eval.yaml": "eval",
    }
    for relative, package_name in packages.items():
        document = scenario["files"].get(relative)
        if not document:
            continue
        parsed = yaml.safe_load(document["text"]) or {}
        if not isinstance(parsed, dict):
            continue
        if package_name is None:
            _merge_mapping(baseline, parsed)
        else:
            package = baseline.setdefault(package_name, {})
            if isinstance(package, dict):
                _merge_mapping(package, parsed)
    return yaml.safe_dump(baseline, sort_keys=False, allow_unicode=True)


def _config_diff_lines(baseline: str, effective: str) -> list[dict[str, str]]:
    """Return escaped-by-Jinja diff rows with stable semantic classes."""
    if not effective:
        return []
    if not baseline:
        return []
    rows = []
    for line in unified_diff(
        baseline.splitlines(),
        effective.splitlines(),
        fromfile="scenario baseline",
        tofile="effective run config",
        lineterm="",
    ):
        if line.startswith(("+++", "---")):
            kind = "header"
        elif line.startswith("+"):
            kind = "addition"
        elif line.startswith("-"):
            kind = "removal"
        elif line.startswith("@@"):
            kind = "hunk"
        else:
            kind = "context"
        rows.append({"kind": kind, "text": line})
    return rows


@router.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, q: str = "", status: str = "", sort: str = "recent"):
    state = studio_state(request)
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


@router.get("/runs/{run_id:path}", response_class=HTMLResponse)
def run_page(
    request: Request,
    run_id: str,
    view: str = "overview",
    tab: str | None = None,
):
    state = studio_state(request)
    studies = state.studies
    record = record_or_404(state, run_id)
    related_job = next(
        (
            job
            for job in state.jobs.store.list()
            if job.output_dir and Path(job.output_dir).resolve() == record.path.resolve()
        ),
        None,
    )
    active_tab = tab or (
        "watch" if related_job and related_job.status in {"queued", "running"} else "overview"
    )
    if active_tab not in {"overview", "watch", "platform", "analyze", "config", "logs"}:
        raise HTTPException(status_code=404, detail=f"Unknown run tab {active_tab!r}")

    # The view name is always validated — ?view=nope is a bad request on any
    # tab — but each tab pays only for its own data: building every panel
    # parses the run's event logs, which the Config and Logs tabs never show.
    selected_view = view_or_404(state, view, record.artifact.scenario)
    built = facets = None
    if active_tab in {"analyze", "watch"}:
        try:
            built = resolve_run_links(
                state,
                build_view(
                    selected_view,
                    record.artifact,
                    panel_param_overrides(request.query_params),
                ),
            )
        except (KeyError, ValueError) as exc:  # unknown panel in a shipped view
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        facets = _run_facets(record.artifact)

    # A run under the studies root belongs to that study; surface the way
    # back so study -> run -> study navigation closes the loop.
    parent_study = None
    run_path = record.path.resolve()
    if run_path.is_relative_to(studies.root.resolve()):
        segments = run_path.relative_to(studies.root.resolve()).parts
        if segments:
            try:
                parent_study = studies.load(
                    segments[0], include_definition=False, include_board=False
                )
            except (KeyError, ValueError):
                parent_study = None

    effective_config = baseline_config = ""
    manifest_text = ""
    if active_tab == "config":
        effective_config = _effective_config_text(record.path)
        baseline_config = _scenario_baseline_text(state.scenarios, record.artifact.scenario)
        manifest_text = yaml.safe_dump(record.artifact.manifest or {}, sort_keys=False)

    log_text = ""
    if active_tab in {"logs", "watch"} and related_job and Path(related_job.log_path).is_file():
        log_text = Path(related_job.log_path).read_text(encoding="utf-8", errors="replace")

    scenario_path = state.repo_root / "scenarios" / str(record.artifact.scenario) / "conf"
    reproduce_command = (
        f"uv run silisocs --config-path {shlex.quote(str(scenario_path))}"
        if record.artifact.scenario and scenario_path.is_dir()
        else f"uv run silisocs-report {shlex.quote(str(record.path))}"
    )
    return state.templates.TemplateResponse(
        request,
        "run.html",
        {
            "record": record,
            "view": built,
            "view_name": view,
            "views": run_view_names(state, record.artifact),
            "facets": facets,
            "watch": (
                _watch_snapshot(record.artifact, related_job, facets)
                if related_job and facets is not None
                else None
            ),
            "tab": active_tab,
            "job": related_job,
            "interactive": bool(related_job and related_job.to_dict().get("interactive")),
            "parent_study": parent_study,
            "viewer_backends": (
                sorted({backend for backend, _ in find_backend_dbs(record.path)})
                if active_tab == "platform"
                else []
            ),
            "effective_config": effective_config,
            "baseline_config": baseline_config,
            "baseline_available": bool(baseline_config),
            "config_diff": _config_diff_lines(baseline_config, effective_config),
            "reproduce_command": reproduce_command,
            "manifest_text": manifest_text,
            "log_text": log_text,
            "active": "runs",
        },
    )


@router.get("/api/runs")
def api_runs(request: Request, q: str = "", status: str = "", sort: str = "recent"):
    records = arrange_runs(
        discover_all_runs(studio_state(request)), query=q, status=status, sort=sort
    )
    return {"items": [run_json(record) for record in records]}


@router.get("/api/runs/{run_id:path}/events/{stream}")
def api_run_events(
    request: Request, run_id: str, stream: str, since_index: int = 0, limit: int = 500
):
    record = record_or_404(studio_state(request), run_id)
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
    return run_json(record_or_404(studio_state(request), run_id))
