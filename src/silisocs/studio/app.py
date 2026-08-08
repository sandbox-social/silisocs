"""API-first FastAPI shell for Silisocs Studio."""
# ruff: noqa: C901, PLC0415
#
# NOTE: no `from __future__ import annotations` here. The route handlers are
# defined inside `create_app` and annotate `request: Request` with a name that
# only exists in that local scope (FastAPI stays an optional extra); stringified
# annotations would make FastAPI unable to resolve it and silently demote the
# request to a required query parameter, 422-ing every page.

import json
import os
import shlex
import socket
import sys
import threading
from contextlib import asynccontextmanager
from dataclasses import replace
from difflib import unified_diff
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

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
from silisocs.analysis.panel import get_panel, list_panels, output_to_dict, serialize_controls
from silisocs.analysis.report import render_report
from silisocs.analysis.views import (
    BUILTIN_VIEWS,
    build_view,
    load_view,
    scenario_view_files,
    skip_reason,
    view_applies,
)
from silisocs.design.assets import CachedAsset
from silisocs.design.css import css_variables
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.catalog import arrange_runs, discover_runs, find_run
from silisocs.studio.forms import (
    ChoiceContext,
    PreviewContext,
    ScenarioRepository,
    choice_items,
    compose_files,
    field_values,
    list_choice_providers,
    list_form_schemas,
    list_preview_providers,
    materialize_form_schema,
    preflight_payload,
    run_choice_provider,
    run_preview_provider,
)
from silisocs.studio.jobs import JobManager, allocate_port
from silisocs.studio.launch import ScenarioNotFoundError, prepare_launch
from silisocs.studio.plugins import load_studio_pages
from silisocs.studio.studies import StudyRepository, compose_study, evaluation_presets
from silisocs.studio.viewers import (
    ViewerDispatch,
    find_backend_dbs,
    viewer_app_factory,
    viewer_mount_path,
    visualizer_plan,
)
from silisocs.studio.workspace import WorkspaceCatalog


def _port_is_serving(port: int, host: str = "127.0.0.1") -> bool:
    """Whether something is accepting connections on a loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex((host, int(port))) == 0


def _coerce_param(value: str) -> Any:
    """Query-string param -> the plain type panels expect."""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"", "none", "all"}:
        return None
    try:
        return int(value)
    except ValueError:
        return value


def _panel_param_overrides(query_params: Any) -> dict[str, dict[str, Any]]:
    """Parse ``p.<panel>.<param>=value`` query args into build_view overrides."""
    overrides: dict[str, dict[str, Any]] = {}
    for key, value in query_params.items():
        if not key.startswith("p."):
            continue
        parts = key.split(".", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            overrides.setdefault(parts[1], {})[parts[2]] = _coerce_param(value)
    return overrides


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
    from silisocs.analysis.panels._shared import backend_types

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


def create_app(
    output_root: str | Path = "outputs",
    *,
    state_dir: str | Path | None = None,
    repo_root: str | Path = ".",
    max_concurrent_runs: int = 1,
    scenario_repositories: tuple[str | Path, ...] = (),
):
    """Create Studio, keeping FastAPI/Jinja imports inside the optional extra."""
    try:
        from fastapi import FastAPI, HTTPException, Request
        from fastapi.responses import HTMLResponse, Response, StreamingResponse
        from fastapi.templating import Jinja2Templates
    except ImportError as exc:
        raise RuntimeError('Silisocs Studio requires: pip install "silisocs[studio]"') from exc

    root = Path(output_root).resolve()
    repository = Path(repo_root).resolve()
    configured_state = state_dir or os.environ.get("SILISOCS_STUDIO_STATE")
    studio_state = Path(configured_state or repository / ".silisocs").expanduser()
    jobs = JobManager(
        studio_state,
        output_root=root,
        max_concurrent_runs=max_concurrent_runs,
    )
    workspace = WorkspaceCatalog(
        repository,
        repository / ".silisocs",
        additional=scenario_repositories,
    )
    scenarios = workspace.scenario_repository()
    studies = StudyRepository(repository / "experiments" / "studies")
    plugin_pages = load_studio_pages()
    package = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(package / "templates"))
    templates.env.globals["plugin_pages"] = plugin_pages

    viewers = ViewerDispatch(root)

    @asynccontextmanager
    async def lifespan(_app):
        threading.Thread(
            target=workspace.extension_catalog,
            daemon=True,
            name="studio-extension-discovery",
        ).start()
        yield
        # Mounted sub-apps never see lifespan events, so their database
        # connections are closed through the dispatcher.
        viewers.close()
        jobs.close()

    app = FastAPI(
        title="Silisocs Studio",
        version="1.0",
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.jobs = jobs
    app.state.workspace = workspace

    def choice_context(source_id: str | None = None) -> ChoiceContext:
        source = workspace.source(source_id)
        return ChoiceContext(
            repository_root=source.path,
            extension_options=lambda kind: workspace.extension_options(
                kind,
                preferred_source=source.id,
            ),
        )

    def project_environment(source_id: str | None = None) -> dict[str, str]:
        selected = workspace.source(source_id)
        ordered = (selected, *(item for item in workspace.sources if item.id != selected.id))
        paths: list[str] = []
        for source in ordered:
            paths.extend(str(path) for path in (source.path / "src", source.path) if path.is_dir())
        inherited = os.environ.get("PYTHONPATH")
        if inherited:
            paths.append(inherited)
        return {"PYTHONPATH": os.pathsep.join(dict.fromkeys(paths))}

    for page in plugin_pages:
        app.include_router(page.router)

    @app.middleware("http")
    async def protect_control_plane(request: Request, call_next):
        token = os.environ.get("STUDIO_AUTH_TOKEN", "")
        client_host = request.client.host if request.client else ""
        local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
        # A browser cannot attach the Bearer header on page loads, so the token
        # may also arrive once as ?token=<value> (set as a cookie below) or via
        # that cookie on subsequent requests.
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        via_query = request.query_params.get("token", "")
        authorized = bool(token) and token in (
            supplied,
            request.cookies.get("studio_token", ""),
            via_query,
        )
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # CSRF: a browser attaches Origin to every cross-origin POST, so a
            # cross-site page cannot forge a control-plane call at localhost.
            # Same-origin Studio fetches and header-less clients (curl) pass.
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host", ""):
                return Response("Cross-site control-plane request rejected", status_code=403)
            if (token and not authorized) or (not token and not local):
                return Response("Studio control-plane authorization required", status_code=401)
        elif token and not local and not authorized:
            # Reads leak run configs and logs; beyond localhost they need the
            # same token as mutations (localhost reads stay open by design).
            return Response("Studio authorization required", status_code=401)
        response = await call_next(request)
        if token and via_query == token:
            response.set_cookie("studio_token", token, httponly=True, samesite="lax")
        return response

    # Study replicate runs live under the studies root (experiments/studies/
    # <id>/runs/...), which is usually outside the output root. They are runs
    # like any other, so they join the catalog under a second discovery root
    # with a stable id prefix — that is what makes a study board row's run
    # reference addressable as a run page at all.
    _STUDY_RUN_PREFIX = "studies/"

    def _studies_root_is_separate() -> bool:
        return not studies.root.resolve().is_relative_to(root)

    def discover_all_runs():
        records = list(discover_runs(root))
        if _studies_root_is_separate():
            # An output-root run already owning an id wins (same precedence as
            # record_or_404), so a literal <root>/studies/... run never coexists
            # with a study replicate under one duplicated id.
            taken = {record.id for record in records}
            records.extend(
                record
                for record in (
                    replace(record, id=f"{_STUDY_RUN_PREFIX}{record.id}")
                    for record in discover_runs(studies.root)
                )
                if record.id not in taken
            )
            records.sort(key=lambda record: record.modified, reverse=True)
        return records

    def record_or_404(run_id: str):
        # Output-root runs win the namespace; the studies/ prefix is only
        # consulted when no output-root run matches.
        try:
            return find_run(root, run_id)
        except KeyError:
            pass
        if run_id.startswith(_STUDY_RUN_PREFIX) and _studies_root_is_separate():
            try:
                record = find_run(studies.root, run_id[len(_STUDY_RUN_PREFIX) :])
                return replace(record, id=run_id)
            except KeyError:
                pass
        raise HTTPException(status_code=404, detail="Run not found")

    def study_or_404(study_id: str, **kwargs: Any) -> dict[str, Any]:
        try:
            return studies.load(study_id, **kwargs)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc

    def scenario_or_404(scenario_name: str, source: str = "workspace") -> dict[str, Any]:
        try:
            return workspace.scenario_repository(source).load(scenario_name)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Scenario not found") from exc

    def require_file_mapping(files: Any) -> dict[str, str]:
        if not isinstance(files, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in files.items()
        ):
            raise HTTPException(
                status_code=422, detail="files must map relative names to YAML text"
            )
        return files

    def view_or_404(view_name: str, scenario: str | None = None):
        # The HTTP surface serves built-in views plus the run's scenario-shipped
        # views (an enumerated, repo-owned file set) — never a free-form path,
        # which would let a request read (and class_path-import) arbitrary files.
        if view_name in BUILTIN_VIEWS:
            return load_view(view_name)
        shipped: dict[str, Path] = {}
        if scenario:
            for source in workspace.sources:
                shipped.update(scenario_view_files(scenario, source.scenario_root))
        if view_name in shipped:
            return load_view(shipped[view_name])
        raise HTTPException(status_code=404, detail=f"Unknown view {view_name!r}")

    def run_view_names(artifact) -> list[str]:
        """The analysis views worth offering for a run.

        Views whose subject the run's backend cannot feed are dropped, so a
        market run has no Network tab and a social run has no Market tab.
        """
        scenario = artifact.scenario
        candidates = [name for name, spec in BUILTIN_VIEWS.items() if spec["scope"] == "run"]
        candidates.extend(scenario_view_files(scenario, scenarios.root) if scenario else ())
        names = []
        for name in candidates:
            try:
                view = view_or_404(name, scenario)
            except (HTTPException, ValueError, OSError, yaml.YAMLError):
                # A scenario-shipped views/*.yaml can be malformed; skip that one
                # candidate rather than 500 every tab of the run page.
                continue
            if view_applies(view, artifact):
                names.append(name)
        return names

    def study_view_or_404(view_name: str, study: dict[str, Any]):
        """Resolve a study-scope view: built-ins plus the study's scenarios' shipped views.

        A study is scoped to the scenarios its definition declares, so a
        scenario may ship a study-scope view (``conf/views/*.yaml`` with
        ``scope: study``) and every study using that scenario can select it —
        the same enumerated, repo-owned allowlist the run routes use. A view
        that resolves but is run-scope is a 404, never a scope error mid-build.
        """
        meta = (study.get("definition") or {}).get("study") or {}
        scenario_names = [name for name in (meta.get("scenarios") or []) if isinstance(name, str)]
        wrong_scope = False
        for scenario in (None, *scenario_names):
            try:
                view = view_or_404(view_name, scenario)
            except HTTPException:
                continue
            # A same-named run-scope view in one scenario must not shadow a
            # study-scope view of that name shipped by another — keep looking.
            if view.scope != "study":
                wrong_scope = True
                continue
            return view
        detail = (
            f"{view_name!r} is not a study view" if wrong_scope else f"Unknown view {view_name!r}"
        )
        raise HTTPException(status_code=404, detail=detail)

    def _shipped_panel(panel_name: str, scope: str, scenario_names: list[str]):
        """Find a view-slot ``class_path`` panel by name in shipped view files.

        One-off panels (referenced only by ``class_path`` in a view, never
        registered) still need to answer the by-name panel endpoints — that is
        what live refresh calls — so resolution falls back to scanning the same
        enumerated view files ``view_or_404`` trusts. Never a free-form import.
        """
        seen: set[str] = set()
        for scenario in scenario_names:
            for source in workspace.sources:
                for path in scenario_view_files(scenario, source.scenario_root).values():
                    if str(path) in seen:
                        continue
                    seen.add(str(path))
                    try:
                        view = load_view(path)
                    except (OSError, ValueError, yaml.YAMLError):
                        continue
                    if view.scope != scope:
                        continue
                    for slot in view.panels:
                        if not slot.class_path:
                            continue
                        try:
                            cls = slot.panel_class()
                        except (ImportError, AttributeError, TypeError, ValueError):
                            continue
                        if cls.name == panel_name:
                            return cls
        return None

    def panel_or_404(panel_name: str, scope: str, scenario_names: list[str]):
        try:
            panel = get_panel(panel_name)
        except KeyError as exc:
            panel = _shipped_panel(panel_name, scope, scenario_names)
            if panel is None:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        if panel.scope != scope:
            raise HTTPException(status_code=404, detail=f"{panel_name!r} is not a {scope} panel")
        return panel

    def _run_href_index() -> dict[str, str]:
        """Resolved run path -> Studio run-page href, across both discovery roots."""
        return {
            record.path.resolve().as_posix(): f"/runs/{record.id}" for record in discover_all_runs()
        }

    def resolve_run_links(payload: dict[str, Any]) -> dict[str, Any]:
        """Resolve run-reference table cells into Studio run links, in place.

        Panels emit portable ``{"run_path", "text"}`` cells (see
        docs/analysis_panels.md — "Run reference cells"); only a shell that can
        address run pages turns them into hrefs, and only here. A path no
        discovery root knows degrades to its text. The run index is built
        lazily, on the first reference actually seen.
        """
        hrefs: dict[str, str] | None = None

        def index() -> dict[str, str]:
            nonlocal hrefs
            if hrefs is None:
                hrefs = _run_href_index()
            return hrefs

        def linkify_cell(value: Any) -> Any:
            if not (isinstance(value, dict) and "run_path" in value):
                return value
            text = str(value.get("text") or "open")
            run_path = str(value.get("run_path") or "")
            href = index().get(Path(run_path).resolve().as_posix()) if run_path else None
            return {"text": text, "href": href} if href else {"text": text}

        def walk(output: Any) -> None:
            if not isinstance(output, dict):
                return
            if output.get("type") == "table":
                output["rows"] = [
                    {key: linkify_cell(cell) for key, cell in row.items()}
                    for row in output.get("rows", [])
                    if isinstance(row, dict)
                ]
            elif output.get("type") == "grid":
                for item in output.get("items", []):
                    walk(item)

        for panel in payload.get("panels", []):
            walk(panel.get("output"))
        walk(payload.get("output"))
        return payload

    def run_json(record) -> dict[str, Any]:
        artifact = record.artifact
        return {
            "id": record.id,
            "scenario": artifact.scenario or record.path.parent.name,
            "status": artifact.status or "unknown",
            "seed": artifact.seed,
            "num_agents": artifact.num_agents,
            "num_steps": artifact.num_steps,
            "llm_name": artifact.llm_name,
            "llm_usage": artifact.llm_usage,
            "health": artifact.health,
            "path": str(record.path),
            "modified": record.modified,
        }

    def study_composer_catalog() -> dict[str, Any]:
        return {
            "evaluation_presets": evaluation_presets(),
            "run_choices": [run_json(record) for record in discover_all_runs()],
            "scenario_choices": workspace.scenarios(),
            "repositories": [source.to_dict() for source in workspace.sources],
        }

    # Read once at startup: these bodies only change when the package does.
    # Brand custom properties (light + dark) come from silisocs.design — Python
    # stays the single source of truth; studio.css holds layout only.
    assets = {
        "tokens.css": CachedAsset.build(
            css_variables() + (package / "static" / "studio.css").read_text(encoding="utf-8"),
            "text/css",
        ),
        "plotly.js": CachedAsset.build(
            (package / "static" / "plotly.min.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        "cytoscape.js": CachedAsset.build(
            (package / "static" / "cytoscape.min.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        "studio.js": CachedAsset.build(
            (package / "static" / "studio.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        "explore.js": CachedAsset.build(
            (package / "static" / "explore.js").read_text(encoding="utf-8"),
            "text/javascript",
        ),
        "manrope.woff2": CachedAsset.build_bytes(
            (package.parent / "design" / "fonts" / "manrope-latin-variable.woff2").read_bytes(),
            "font/woff2",
        ),
    }

    @app.get("/assets/{name}")
    def asset(request: Request, name: str):
        cached = assets.get(name)
        if cached is None:
            raise HTTPException(status_code=404, detail=f"Unknown asset {name!r}")
        status, body, headers = cached.response(request.headers.get("if-none-match"))
        if status == 304:
            return Response(status_code=304, headers=headers)
        return Response(body, media_type=cached.media_type, headers=headers)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        runs = discover_all_runs()
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "runs": runs[:8],
                "all_runs": runs,
                "active": "home",
                "plugin_pages": plugin_pages,
                "scenario_count": len(workspace.scenarios()),
                "study_count": studies.count(),
                "live_count": len(
                    [job for job in jobs.store.list() if job.status in {"queued", "running"}]
                ),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "panels": list_panels(),
                "views": BUILTIN_VIEWS,
                "plugin_pages": plugin_pages,
                "form_schemas": list_form_schemas(),
                "choice_providers": list_choice_providers(),
                "preview_providers": list_preview_providers(),
                "repositories": [source.to_dict() for source in workspace.sources],
                "extension_catalog": workspace.extension_catalog(),
                "discovery_errors": workspace.discovery_errors,
                "active": "settings",
            },
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request, q: str = "", status: str = "", sort: str = "recent"):
        records = discover_all_runs()
        statuses = sorted({record.artifact.status or "unknown" for record in records})
        return templates.TemplateResponse(
            request,
            "runs.html",
            {
                "runs": arrange_runs(records, query=q, status=status, sort=sort),
                "statuses": statuses,
                "filters": {"q": q, "status": status, "sort": sort},
                "active": "runs",
            },
        )

    @app.get("/live", response_class=HTMLResponse)
    def live_page(request: Request, job: str | None = None):
        selected = None
        if job:
            try:
                selected = jobs.store.get(job)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Job not found") from exc
        all_jobs = jobs.store.list()
        run_id = None
        if selected and selected.output_dir:
            match = next(
                (
                    record
                    for record in discover_all_runs()
                    if record.path.resolve() == Path(selected.output_dir).resolve()
                ),
                None,
            )
            run_id = match.id if match else None
        interactive = bool(selected and selected.to_dict().get("interactive"))
        return templates.TemplateResponse(
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

    @app.get("/studies", response_class=HTMLResponse)
    def studies_page(request: Request):
        return templates.TemplateResponse(
            request,
            "studies.html",
            {"studies": studies.list(), "active": "studies"},
        )

    @app.get("/studies/new", response_class=HTMLResponse)
    def new_study_page(request: Request, name: str = "new_study"):
        try:
            study_id = studies.validate_id(name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        scenario_choices = workspace.scenarios()
        definition = studies.new_definition(
            study_id,
            scenario=scenario_choices[0] if scenario_choices else None,
            working_directory=str(repository),
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
        return templates.TemplateResponse(
            request,
            "study.html",
            {
                "study": study,
                "view": None,
                "tab": "definition",
                "job": None,
                "active": "studies",
                "notebook_exists": False,
                **study_composer_catalog(),
            },
        )

    @app.get("/studies/{study_id}", response_class=HTMLResponse)
    def study_page(request: Request, study_id: str, view: str = "progress", tab: str = "board"):
        study = study_or_404(study_id)
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
                build_view(
                    study_view_or_404(selected_view, study),
                    load_study(study["path"]),
                    _panel_param_overrides(request.query_params),
                )
            )
            if tab != "definition"
            else None
        )
        related = next((job for job in jobs.store.list() if job.parent_study == study_id), None)
        return templates.TemplateResponse(
            request,
            "study.html",
            {
                "study": study,
                "view": built,
                "tab": tab,
                "job": related,
                "active": "studies",
                "notebook_exists": (Path(study["path"]) / "notebook.ipynb").is_file(),
                **(study_composer_catalog() if tab == "definition" else {}),
            },
        )

    @app.get("/explore/study/{study_id}", response_class=HTMLResponse)
    def explore_study_page(request: Request, study_id: str):
        study = study_or_404(study_id)
        try:
            state = ExplorationState.from_query("study", (study_id,), request.query_params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        artifact = load_study(study["path"])
        return templates.TemplateResponse(
            request,
            "explore_study.html",
            {
                "study": study,
                "state": state.to_dict(),
                "capabilities": study_capability_document(artifact, study_id),
                "active": "studies",
            },
        )

    @app.get("/scenarios", response_class=HTMLResponse)
    def scenarios_page(request: Request):
        return templates.TemplateResponse(
            request,
            "scenarios.html",
            {"scenarios": workspace.scenarios(), "active": "scenarios"},
        )

    @app.get("/scenarios/new", response_class=HTMLResponse)
    def new_scenario_page(request: Request, name: str = "new_scenario"):
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
        return templates.TemplateResponse(
            request,
            "scenario.html",
            {
                "scenario": scenario,
                "schema": materialize_form_schema(
                    files,
                    defer_expensive=True,
                    choice_context=choice_context(),
                ),
                "values": field_values(files),
                "panel_catalog": [],
                "history": [],
                "active": "scenarios",
            },
        )

    @app.get("/scenarios/{scenario_name}", response_class=HTMLResponse)
    def scenario_page(request: Request, scenario_name: str, source: str = "workspace"):
        scenario = scenario_or_404(scenario_name, source)
        scenario["source"] = source
        scenario["source_label"] = workspace.source(source).label
        text_files = {name: item["text"] for name, item in scenario["files"].items()}
        return templates.TemplateResponse(
            request,
            "scenario.html",
            {
                "scenario": scenario,
                "schema": materialize_form_schema(
                    text_files,
                    defer_expensive=True,
                    choice_context=choice_context(source),
                ),
                "values": field_values(text_files),
                "panel_catalog": [],
                "active": "scenarios",
            },
        )

    @app.get("/runs/{run_id:path}", response_class=HTMLResponse)
    def run_page(
        request: Request,
        run_id: str,
        view: str = "overview",
        tab: str | None = None,
    ):
        record = record_or_404(run_id)
        related_job = next(
            (
                job
                for job in jobs.store.list()
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
        selected_view = view_or_404(view, record.artifact.scenario)
        built = facets = None
        if active_tab in {"analyze", "watch"}:
            try:
                built = resolve_run_links(
                    build_view(
                        selected_view,
                        record.artifact,
                        _panel_param_overrides(request.query_params),
                    )
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
            baseline_config = _scenario_baseline_text(scenarios, record.artifact.scenario)
            manifest_text = yaml.safe_dump(record.artifact.manifest or {}, sort_keys=False)

        log_text = ""
        if active_tab in {"logs", "watch"} and related_job and Path(related_job.log_path).is_file():
            log_text = Path(related_job.log_path).read_text(encoding="utf-8", errors="replace")

        scenario_path = repository / "scenarios" / str(record.artifact.scenario) / "conf"
        reproduce_command = (
            f"uv run silisocs --config-path {shlex.quote(str(scenario_path))}"
            if record.artifact.scenario and scenario_path.is_dir()
            else f"uv run silisocs-report {shlex.quote(str(record.path))}"
        )
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "record": record,
                "view": built,
                "view_name": view,
                "views": run_view_names(record.artifact),
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

    @app.get("/explore/run/{run_id:path}", response_class=HTMLResponse)
    def explore_run_page(request: Request, run_id: str):
        record = record_or_404(run_id)
        try:
            state = ExplorationState.from_query("run", (run_id,), request.query_params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        capabilities = run_capability_document(record.artifact, run_id)
        return templates.TemplateResponse(
            request,
            "explore.html",
            {
                "record": record,
                "state": state.to_dict(),
                "capabilities": capabilities,
                "active": "runs",
            },
        )

    @app.get("/explore/compare", response_class=HTMLResponse)
    def explore_compare_page(request: Request):
        # The template guards on <2 records; parsing is shared with the API route.
        records = [record_or_404(run_id) for run_id in compare_run_ids(request)]
        return templates.TemplateResponse(
            request,
            "explore_compare.html",
            {"records": records, "run_ids": [record.id for record in records], "active": "runs"},
        )

    @app.get("/api/runs")
    def api_runs(q: str = "", status: str = "", sort: str = "recent"):
        records = arrange_runs(discover_all_runs(), query=q, status=status, sort=sort)
        return {"items": [run_json(record) for record in records]}

    @app.get("/api/scenarios")
    def api_scenarios():
        return {"items": workspace.scenarios()}

    @app.get("/api/repositories")
    def api_repositories():
        return {
            "items": [source.to_dict() for source in workspace.sources],
            "extensions": workspace.extension_catalog(),
            "discovery_errors": workspace.discovery_errors,
        }

    @app.post("/api/repositories")
    async def api_add_repository(request: Request):
        payload = await request.json()
        try:
            source = workspace.add(
                str(payload.get("path") or ""),
                nickname=str(payload.get("nickname") or ""),
            )
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "source": source.to_dict(),
            "scenarios": workspace.scenario_repository(source.id).list(),
            "extensions": workspace.extension_catalog(),
        }

    @app.patch("/api/repositories/{source_id}")
    async def api_rename_repository(source_id: str, request: Request):
        payload = await request.json()
        try:
            source = workspace.rename(source_id, str(payload.get("nickname") or ""))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"source": source.to_dict()}

    @app.post("/api/repositories/refresh")
    def api_refresh_repositories():
        workspace.refresh_extensions()
        return {
            "extensions": workspace.extension_catalog(),
            "discovery_errors": workspace.discovery_errors,
        }

    @app.delete("/api/repositories/{source_id}")
    def api_remove_repository(source_id: str):
        try:
            workspace.remove(source_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"removed": source_id}

    @app.get("/api/studies")
    def api_studies():
        return {"items": studies.list()}

    @app.get("/api/studies/{study_id}")
    def api_study(study_id: str):
        return study_or_404(study_id)

    @app.get("/api/studies/{study_id}/panels/{panel_name}")
    def api_study_panel(request: Request, study_id: str, panel_name: str):
        # Mirror of the run-panel endpoint at study scope, so live surfaces
        # refresh ANY study panel through the shared renderPanel path instead of
        # rebuilding a specific panel's output shape by hand.
        study = study_or_404(study_id, include_board=False)
        meta = (study.get("definition") or {}).get("study") or {}
        scenario_names = [name for name in (meta.get("scenarios") or []) if isinstance(name, str)]
        panel = panel_or_404(panel_name, "study", scenario_names)
        artifact = load_study(study["path"])
        reason = skip_reason(panel, artifact)
        if reason:
            raise HTTPException(status_code=409, detail=f"Panel {panel_name!r} {reason}")
        params = {
            key: _coerce_param(value)
            for key, value in request.query_params.items()
            if not key.startswith("p.")
        }
        return resolve_run_links(
            {
                "name": panel.name,
                "title": panel.title,
                "params": params,
                "controls": serialize_controls(panel, params),
                "output": output_to_dict(panel().build(artifact, params)),
            }
        )

    @app.get("/api/studies/{study_id}/compare")
    def api_study_compare(
        study_id: str,
        compare: str | None = None,
        baseline: str | None = None,
        hypothesis: str | None = None,
        panel: str = "condition_comparison",
        view: str = "comparison",
    ):
        # `panel`/`view` let a study ship its own comparison panel and still be
        # driven by the same query params, instead of hard-wiring the built-in.
        study = study_or_404(study_id)
        compare_view = study_view_or_404(view, study)
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
            build_view(
                compare_view,
                load_study(study["path"]),
                param_overrides={panel: params} if params else None,
            )
        )

    @app.get("/api/explore/studies/{study_id}/capabilities")
    def api_explore_study_capabilities(study_id: str):
        study = study_or_404(study_id, include_definition=False, include_board=False)
        return study_capability_document(load_study(study["path"]), study_id)

    @app.get("/api/studies/{study_id}/notebook")
    def api_study_notebook(study_id: str):
        study = study_or_404(study_id, include_definition=False, include_board=False)
        path = Path(study["path"]) / "notebook.ipynb"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Study notebook not found")
        return Response(path.read_bytes(), media_type="application/x-ipynb+json")

    @app.post("/api/studies/{study_id}")
    async def api_save_study(request: Request, study_id: str):
        payload = await request.json()
        try:
            return studies.save(study_id, str(payload.get("yaml") or ""))
        except (KeyError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/studies/{study_id}/compose")
    async def api_compose_study(request: Request, study_id: str):
        try:
            studies.validate_id(study_id)
            payload = await request.json()
            return compose_study(
                str(payload.get("yaml") or ""),
                dict(payload.get("updates") or {}),
            )
        except (KeyError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/studies/{study_id}/launch")
    async def api_launch_study(request: Request, study_id: str):
        study = study_or_404(study_id)
        payload = await request.json()
        concurrency = max(1, int(payload.get("max_concurrent", 1) or 1))
        command = [
            sys.executable,
            "-m",
            "silisocs.studies.run_study",
            "--study",
            study["definition_path"],
            "--repo-root",
            str(repository),
            "run",
            "--max-concurrent",
            str(concurrency),
            "--yes",
        ]
        job = jobs.submit(
            kind="study_run",
            command=command,
            cwd=repository,
            scenario=study_id,
            snapshot={"study": study["definition"], "command": command},
            output_dir=study["path"],
            parent_study=study_id,
            env=project_environment(),
        )
        return job.to_dict()

    @app.get("/api/scenarios/{scenario_name}")
    def api_scenario(scenario_name: str, source: str = "workspace"):
        scenario = scenario_or_404(scenario_name, source)
        files = {name: item["text"] for name, item in scenario["files"].items()}
        return {
            **scenario,
            "source": source,
            "schema": materialize_form_schema(
                files,
                defer_expensive=True,
                choice_context=choice_context(source),
            ),
            "values": field_values(files),
        }

    @app.get("/api/scenarios/{scenario_name}/runs")
    def api_scenario_runs(scenario_name: str, source: str = "workspace"):
        scenario_or_404(scenario_name, source)
        return {
            "items": [
                run_json(record)
                for record in discover_runs(root, subtree=scenario_name)
                if record.artifact.scenario == scenario_name
            ]
        }

    @app.post("/api/scenarios")
    async def api_save_scenario(request: Request):
        payload = await request.json()
        name = str(payload.get("name") or "")
        source = str(payload.get("source") or "workspace")
        files = require_file_mapping(payload.get("files") or {})
        try:
            return workspace.scenario_repository(source).save(name, files)
        except (KeyError, ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/compose")
    async def api_compose(request: Request):
        payload = await request.json()
        try:
            files = compose_files(
                dict(payload.get("files") or {}), dict(payload.get("updates") or {})
            )
            return {
                "files": files,
                "values": field_values(files),
                "schema": materialize_form_schema(
                    files,
                    defer_expensive=True,
                    choice_context=choice_context(str(payload.get("source") or "workspace")),
                ),
            }
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/preflight")
    async def api_preflight(request: Request):
        payload = await request.json()
        files = payload.get("files")
        if files is None and payload.get("scenario"):
            loaded = scenario_or_404(
                str(payload["scenario"]), str(payload.get("source") or "workspace")
            )
            files = {name: item["text"] for name, item in loaded["files"].items()}
        if not isinstance(files, dict):
            raise HTTPException(status_code=422, detail="files or scenario is required")
        return preflight_payload(files)

    @app.post("/api/form-preview")
    async def api_form_preview(request: Request):
        payload = await request.json()
        files = payload.get("files") or {}
        provider = str(payload.get("provider") or "")
        item_key = str(payload.get("item_key") or "")
        require_file_mapping(files)
        try:
            return run_preview_provider(
                provider,
                files,
                item_key,
                PreviewContext(
                    repository_root=workspace.source(str(payload.get("source") or "workspace")).path
                ),
            )
        except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/form-choices")
    async def api_form_choices(request: Request):
        import asyncio

        payload = await request.json()
        files = require_file_mapping(payload.get("files") or {})
        provider = str(payload.get("provider") or "")
        try:
            context = choice_context(str(payload.get("source") or "workspace"))
            choices = await asyncio.to_thread(run_choice_provider, provider, files, context)
        except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"choices": choices, "items": choice_items(provider, choices, context)}

    @app.get("/api/runs/{run_id:path}/events/{stream}")
    def api_run_events(run_id: str, stream: str, since_index: int = 0, limit: int = 500):
        record = record_or_404(run_id)
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

    @app.get("/api/explore/runs/{run_id:path}/capabilities")
    def api_explore_run_capabilities(run_id: str):
        record = record_or_404(run_id)
        return run_capability_document(record.artifact, run_id)

    def exploration_query(request: Request) -> ExplorationQuery:
        try:
            return ExplorationQuery.from_mapping(request.query_params)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/explore/runs/{run_id:path}/events")
    def api_explore_run_events(request: Request, run_id: str):
        record = record_or_404(run_id)
        return query_events(record.artifact, exploration_query(request))

    @app.get("/api/explore/runs/{run_id:path}/entities")
    def api_explore_run_entities(request: Request, run_id: str):
        record = record_or_404(run_id)
        return query_entities(record.artifact, exploration_query(request))

    @app.get("/api/explore/runs/{run_id:path}/series")
    def api_explore_run_series(request: Request, run_id: str):
        record = record_or_404(run_id)
        return query_series(record.artifact, exploration_query(request))

    @app.get("/api/explore/runs/{run_id:path}/relationships")
    def api_explore_run_relationships(request: Request, run_id: str):
        record = record_or_404(run_id)
        return query_relationships(record.artifact, exploration_query(request))

    @app.get("/api/explore/runs/{run_id:path}/evidence")
    def api_explore_run_evidence(request: Request, run_id: str):
        record = record_or_404(run_id)
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

    @app.get("/api/explore/runs/{run_id:path}/story")
    def api_explore_run_story(run_id: str):
        record = record_or_404(run_id)
        return run_story(record.artifact)

    def compare_run_ids(request: Request) -> list[str]:
        ids = list(request.query_params.getlist("runs"))
        if len(ids) == 1 and "," in ids[0]:
            ids = ids[0].split(",")
        return [run_id.strip() for run_id in ids if run_id.strip()]

    @app.get("/api/explore/compare")
    def api_explore_compare(request: Request):
        run_ids = compare_run_ids(request)
        if len(run_ids) < 2:
            raise HTTPException(status_code=422, detail="Provide at least two runs to compare")
        artifacts = [(run_id, record_or_404(run_id).artifact) for run_id in run_ids]
        return compare_runs(artifacts, exploration_query(request))

    @app.get("/api/runs/{run_id:path}/views/{view_name}")
    def api_run_view(request: Request, run_id: str, view_name: str):
        record = record_or_404(run_id)
        try:
            return resolve_run_links(
                build_view(
                    view_or_404(view_name, record.artifact.scenario),
                    record.artifact,
                    _panel_param_overrides(request.query_params),
                )
            )
        except (KeyError, ValueError) as exc:  # unknown panel in a shipped view
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id:path}/panels/{panel_name}")
    def api_run_panel(request: Request, run_id: str, panel_name: str):
        record = record_or_404(run_id)
        scenario_names = [record.artifact.scenario] if record.artifact.scenario else []
        panel = panel_or_404(panel_name, "run", scenario_names)
        reason = skip_reason(panel, record.artifact)
        if reason:
            raise HTTPException(status_code=409, detail=f"Panel {panel_name!r} {reason}")
        params = {
            key: _coerce_param(value)
            for key, value in request.query_params.items()
            if not key.startswith("p.")
        }
        return resolve_run_links(
            {
                "name": panel.name,
                "title": panel.title,
                "params": params,
                "controls": serialize_controls(panel, params),
                "output": output_to_dict(panel().build(record.artifact, params)),
            }
        )

    @app.get("/api/runs/{run_id:path}/report")
    def api_run_report(run_id: str, view: str = "overview"):
        record = record_or_404(run_id)
        report_view = view_or_404(view, record.artifact.scenario)
        if report_view.scope != "run":
            raise HTTPException(status_code=404, detail=f"Unknown report view {view!r}")
        document = render_report(record.path, report_view)
        filename = f"{(record.artifact.scenario or 'run')}-{view}.html"
        return Response(
            document,
            media_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/runs/{run_id:path}")
    def api_run(run_id: str):
        return run_json(record_or_404(run_id))

    @app.get("/api/jobs")
    def api_jobs():
        return {"items": [job.to_dict() for job in jobs.store.list()]}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        try:
            return jobs.store.get(job_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @app.post("/api/jobs/{job_id}/stop")
    def api_stop_job(job_id: str):
        try:
            return jobs.stop(job_id).to_dict()
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @app.get("/api/jobs/{job_id}/stream")
    def api_job_stream(job_id: str):
        try:
            jobs.store.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

        def stream():
            for item in jobs.events(job_id):
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'])}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/launch")
    async def api_launch(request: Request):
        payload = await request.json()
        source_id = str(payload.pop("source", None) or "workspace")
        try:
            source = workspace.source(source_id)
            spec = prepare_launch(
                payload,
                repository_root=repository,
                output_root=root,
                draft_root=studio_state / "launch_configs",
            )
        except (KeyError, ScenarioNotFoundError) as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = jobs.submit(
            kind="run",
            command=spec.command,
            cwd=source.path,
            scenario=spec.scenario,
            snapshot=spec.snapshot,
            output_dir=spec.output_dir,
            env=project_environment(source_id),
            control_path=spec.control_path,
        )
        return job.to_dict()

    @app.post("/api/jobs/{job_id}/control")
    async def api_job_control(request: Request, job_id: str):
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Invalid JSON body") from exc
        try:
            return jobs.control(job_id, body if isinstance(body, dict) else {})
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/viewers/{run_id:path}/{backend_type}")
    def api_start_viewer(run_id: str, backend_type: str):
        record = record_or_404(run_id)
        # A viewer declaring an ASGI factory is mounted in-process and needs no
        # process, port, or readiness wait: it is ready the moment we answer.
        if viewer_app_factory(backend_type) is not None:
            return {"mode": "embedded", "url": viewer_mount_path(run_id, backend_type)}
        identity = f"{run_id}:{backend_type}"
        existing = next(
            (
                job
                for job in jobs.store.list()
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
        match = next(
            (item for item in find_backend_dbs(record.path) if item[0] == backend_type), None
        )
        if match is None:
            raise HTTPException(status_code=404, detail="Visualizer database not found")
        port = allocate_port()
        plan = visualizer_plan(match[0], match[1], port=port)
        if plan.missing_extra:
            raise HTTPException(
                status_code=409,
                detail=f'Install the "{plan.missing_extra}" extra to launch this viewer',
            )
        job = jobs.submit(
            kind="viewer",
            command=plan.cmd,
            cwd=repository,
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

    @app.get("/api/viewers/{run_id:path}/{backend_type}/status")
    def api_viewer_status(run_id: str, backend_type: str):
        """Whether a subprocess viewer is actually serving yet.

        A launched process is not a bound port: the job goes 'running' at spawn,
        while the server only listens once its imports finish. Clients must wait
        on this, not on the job status.
        """
        record_or_404(run_id)
        identity = f"{run_id}:{backend_type}"
        job = next(
            (
                item
                for item in jobs.store.list()
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

    @app.get("/api/panels")
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

    @app.get("/api/views")
    def api_views():
        return {"items": [{"name": name, **spec} for name, spec in BUILTIN_VIEWS.items()]}

    @app.get("/api/forms")
    def api_forms():
        return {
            "items": [schema.to_dict() for schema in list_form_schemas()],
            "choice_providers": list(list_choice_providers()),
            "preview_providers": list(list_preview_providers()),
        }

    # Mounted last: a mount matches by prefix, so it must not shadow the routes
    # above. Viewer apps are built on first request, not here — runs are
    # discovered per request.
    app.mount("/viewers", viewers)
    app.state.viewers = viewers

    return app
