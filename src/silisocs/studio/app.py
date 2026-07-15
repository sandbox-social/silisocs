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
import sys
from pathlib import Path
from typing import Any

import yaml

from silisocs.analysis.panel import get_panel, list_panels, output_to_dict
from silisocs.analysis.report import render_report
from silisocs.analysis.views import (
    BUILTIN_VIEWS,
    build_view,
    load_view,
    missing_requirements,
    scenario_view_files,
)
from silisocs.dashboard.viewers import find_backend_dbs, visualizer_plan
from silisocs.design.css import css_variables
from silisocs.evaluations.run_artifact import load_study
from silisocs.studio.catalog import discover_runs, find_run
from silisocs.studio.forms import (
    PreviewContext,
    ScenarioRepository,
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
    agents: set[str] = set()
    probes: set[str] = set()
    for row in artifact.iter_actions():
        episode = row.get("episode")
        if isinstance(episode, int):
            max_episode = max(max_episode, episode)
        source = row.get("source_user")
        if source:
            agents.add(str(source))
    for row in artifact.iter_probes():
        episode = row.get("episode")
        if isinstance(episode, int):
            max_episode = max(max_episode, episode)
        if row.get("label"):
            probes.add(str(row["label"]))
    return {"max_episode": max_episode, "agents": sorted(agents), "probes": sorted(probes)}


def _effective_config_text(run_dir: Path) -> str:
    candidates = [
        run_dir / "effective_config.yaml",
        *run_dir.glob("configs/**/effective_config.yaml"),
    ]
    candidate = next((path for path in candidates if path.is_file()), None)
    return candidate.read_text(encoding="utf-8") if candidate else ""


def create_app(
    output_root: str | Path = "outputs",
    *,
    state_dir: str | Path | None = None,
    repo_root: str | Path = ".",
    max_concurrent_runs: int = 1,
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
    studio_state = Path(
        state_dir or os.environ.get("SILISOCS_STUDIO_STATE", "~/.silisocs")
    ).expanduser()
    jobs = JobManager(
        studio_state,
        output_root=root,
        max_concurrent_runs=max_concurrent_runs,
    )
    scenarios = ScenarioRepository(repository / "scenarios")
    studies = StudyRepository(repository / "experiments" / "studies")
    plugin_pages = load_studio_pages()
    package = Path(__file__).resolve().parent
    templates = Jinja2Templates(directory=str(package / "templates"))
    templates.env.globals["plugin_pages"] = plugin_pages
    app = FastAPI(title="Silisocs Studio", version="1.0", docs_url="/api/docs", redoc_url=None)
    app.state.jobs = jobs

    @app.on_event("shutdown")
    def close_job_manager() -> None:
        jobs.close()

    for page in plugin_pages:
        app.include_router(page.router)

    @app.middleware("http")
    async def protect_mutations(request: Request, call_next):
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return await call_next(request)
        token = os.environ.get("STUDIO_AUTH_TOKEN", "")
        client_host = request.client.host if request.client else ""
        local = client_host in {"127.0.0.1", "::1", "localhost", "testclient"}
        supplied = request.headers.get("authorization", "").removeprefix("Bearer ")
        if (token and supplied != token) or (not token and not local):
            return Response("Studio control-plane authorization required", status_code=401)
        return await call_next(request)

    def record_or_404(run_id: str):
        try:
            return find_run(root, run_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Run not found") from exc

    def view_or_404(view_name: str, scenario: str | None = None):
        # The HTTP surface serves built-in views plus the run's scenario-shipped
        # views (an enumerated, repo-owned file set) — never a free-form path,
        # which would let a request read (and class_path-import) arbitrary files.
        if view_name in BUILTIN_VIEWS:
            return load_view(view_name)
        shipped = scenario_view_files(scenario, scenarios.root) if scenario else {}
        if view_name in shipped:
            return load_view(shipped[view_name])
        raise HTTPException(status_code=404, detail=f"Unknown view {view_name!r}")

    def run_view_names(scenario: str | None) -> list[str]:
        names = [name for name, spec in BUILTIN_VIEWS.items() if spec["scope"] == "run"]
        names.extend(scenario_view_files(scenario, scenarios.root) if scenario else ())
        return names

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
            "run_choices": [run_json(record) for record in discover_runs(root)],
        }

    @app.get("/assets/tokens.css")
    def tokens_css():
        # Brand custom properties (light + dark) come from silisocs.design —
        # Python stays the single source of truth; studio.css holds layout only.
        stylesheet = (package / "static" / "studio.css").read_text(encoding="utf-8")
        return Response(css_variables() + stylesheet, media_type="text/css")

    @app.get("/assets/plotly.js")
    def plotly_js():
        return Response(
            (package / "static" / "plotly.min.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
        )

    @app.get("/assets/cytoscape.js")
    def cytoscape_js():
        return Response(
            (package / "static" / "cytoscape.min.js").read_text(encoding="utf-8"),
            media_type="text/javascript",
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        runs = discover_runs(root)
        return templates.TemplateResponse(
            request,
            "home.html",
            {
                "runs": runs[:8],
                "all_runs": runs,
                "active": "home",
                "plugin_pages": plugin_pages,
                "scenario_count": scenarios.count(),
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
                "active": "settings",
            },
        )

    @app.get("/runs", response_class=HTMLResponse)
    def runs_page(request: Request):
        return templates.TemplateResponse(
            request, "runs.html", {"runs": discover_runs(root), "active": "runs"}
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
                    for record in discover_runs(root)
                    if record.path.resolve() == Path(selected.output_dir).resolve()
                ),
                None,
            )
            run_id = match.id if match else None
        return templates.TemplateResponse(
            request,
            "live.html",
            {"jobs": all_jobs, "job": selected, "run_id": run_id, "active": "live"},
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
        definition = {
            "schema_version": 1,
            "study": {
                "name": study_id,
                "study_id": study_id,
                "study_version": "v1",
                "question": "",
                "scenarios": ["default"],
                "run_defaults": {
                    "config_path": "scenarios/default/conf",
                    "runner_module": "silisocs.runtime.runner",
                    "seed_start": 1,
                    "seed_repeats": 1,
                    "overrides": {},
                },
            },
            "evaluations": [],
            "hypotheses": {
                "h1": {
                    "statement": "",
                    "independent_variable": "",
                    "prediction": "",
                    "status": "planning",
                    "conditions": {"baseline": {"overrides": {}}},
                }
            },
        }
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
        try:
            study = studies.load(study_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        selected_view = {
            "board": "progress",
            "compare": "comparison",
            "hypotheses": "hypotheses",
        }.get(tab, view)
        built = (
            build_view(load_view(selected_view), load_study(study["path"]))
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

    @app.get("/scenarios", response_class=HTMLResponse)
    def scenarios_page(request: Request):
        return templates.TemplateResponse(
            request,
            "scenarios.html",
            {"scenarios": scenarios.list(), "active": "scenarios"},
        )

    @app.get("/scenarios/new", response_class=HTMLResponse)
    def new_scenario_page(request: Request, name: str = "new_scenario"):
        try:
            safe_name = scenarios.validate_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        files = {
            "world/default.yaml": yaml.safe_dump(
                {
                    "scenario_name": safe_name,
                    "num_agents": 4,
                    "num_steps": 5,
                    "seed": 1,
                    "setting": {"name": "", "background": []},
                    "event": {"name": "", "context": ""},
                },
                sort_keys=False,
            ),
            "agents/default.yaml": "shared_memories: []\npersona_pipeline:\n  classes: {}\n",
            "sim.yaml": "{}\n",
            "env.yaml": "gm:\n  backend:\n    type: twitter_like\n",
            "eval.yaml": "probes:\n  enabled: false\n",
        }
        scenario = {
            "name": safe_name,
            "path": str(scenarios.root / safe_name),
            "files": {
                key: {"text": value, "data": yaml.safe_load(value)} for key, value in files.items()
            },
        }
        return templates.TemplateResponse(
            request,
            "scenario.html",
            {
                "scenario": scenario,
                "schema": materialize_form_schema(files, defer_expensive=True),
                "values": field_values(files),
                "panel_catalog": [panel.name for panel in list_panels() if panel.scope == "run"],
                "history": [],
                "active": "scenarios",
            },
        )

    @app.get("/scenarios/{scenario_name}", response_class=HTMLResponse)
    def scenario_page(request: Request, scenario_name: str):
        try:
            scenario = scenarios.load(scenario_name)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Scenario not found") from exc
        text_files = {name: item["text"] for name, item in scenario["files"].items()}
        return templates.TemplateResponse(
            request,
            "scenario.html",
            {
                "scenario": scenario,
                "schema": materialize_form_schema(text_files, defer_expensive=True),
                "values": field_values(text_files),
                "panel_catalog": [panel.name for panel in list_panels() if panel.scope == "run"],
                "history": [
                    record
                    for record in discover_runs(root)
                    if record.artifact.scenario == scenario_name
                ],
                "active": "scenarios",
            },
        )

    @app.get("/runs/{run_id:path}", response_class=HTMLResponse)
    def run_page(request: Request, run_id: str, view: str = "overview", tab: str = "analyze"):
        record = record_or_404(run_id)
        overrides = _panel_param_overrides(request.query_params)
        try:
            built = build_view(
                view_or_404(view, record.artifact.scenario), record.artifact, overrides
            )
        except (KeyError, ValueError) as exc:  # unknown panel in a shipped view
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        related_job = next(
            (
                job
                for job in jobs.store.list()
                if job.output_dir and Path(job.output_dir).resolve() == record.path.resolve()
            ),
            None,
        )
        return templates.TemplateResponse(
            request,
            "run.html",
            {
                "record": record,
                "view": built,
                "view_name": view,
                "views": run_view_names(record.artifact.scenario),
                "facets": _run_facets(record.artifact),
                "tab": tab,
                "job": related_job,
                "viewer_backends": sorted(
                    {backend for backend, _ in find_backend_dbs(record.path)}
                ),
                "effective_config": _effective_config_text(record.path),
                "manifest_text": yaml.safe_dump(record.artifact.manifest or {}, sort_keys=False),
                "log_text": (
                    Path(related_job.log_path).read_text(encoding="utf-8", errors="replace")
                    if related_job and Path(related_job.log_path).is_file()
                    else ""
                ),
                "active": "runs",
            },
        )

    @app.get("/api/runs")
    def api_runs():
        return {"items": [run_json(record) for record in discover_runs(root)]}

    @app.get("/api/scenarios")
    def api_scenarios():
        return {"items": scenarios.list()}

    @app.get("/api/studies")
    def api_studies():
        return {"items": studies.list()}

    @app.get("/api/studies/{study_id}")
    def api_study(study_id: str):
        try:
            return studies.load(study_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc

    @app.get("/api/studies/{study_id}/board")
    def api_study_board(study_id: str):
        try:
            return {"items": studies.load(study_id)["board"]}
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc

    @app.get("/api/studies/{study_id}/compare")
    def api_study_compare(study_id: str):
        try:
            study = studies.load(study_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        return build_view(load_view("comparison"), load_study(study["path"]))

    @app.get("/api/studies/{study_id}/notebook")
    def api_study_notebook(study_id: str):
        try:
            study = studies.load(study_id, include_definition=False, include_board=False)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
        path = Path(study["path"]) / "notebook.ipynb"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Study notebook not found")
        return Response(path.read_bytes(), media_type="application/x-ipynb+json")

    @app.post("/api/studies/{study_id}")
    async def api_save_study(request: Request, study_id: str):
        payload = await request.json()
        try:
            return studies.save(study_id, str(payload.get("yaml") or ""))
        except (ValueError, yaml.YAMLError) as exc:
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
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/studies/{study_id}/launch")
    async def api_launch_study(request: Request, study_id: str):
        try:
            study = studies.load(study_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Study not found") from exc
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
        )
        return job.to_dict()

    @app.get("/api/scenarios/{scenario_name}")
    def api_scenario(scenario_name: str):
        try:
            scenario = scenarios.load(scenario_name)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=404, detail="Scenario not found") from exc
        files = {name: item["text"] for name, item in scenario["files"].items()}
        return {
            **scenario,
            "schema": materialize_form_schema(files, defer_expensive=True),
            "values": field_values(files),
        }

    @app.post("/api/scenarios")
    async def api_save_scenario(request: Request):
        payload = await request.json()
        name = str(payload.get("name") or "")
        files = payload.get("files") or {}
        if not isinstance(files, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in files.items()
        ):
            raise HTTPException(
                status_code=422, detail="files must map relative names to YAML text"
            )
        try:
            return scenarios.save(name, files)
        except (ValueError, yaml.YAMLError) as exc:
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
                "schema": materialize_form_schema(files, defer_expensive=True),
            }
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/preflight")
    async def api_preflight(request: Request):
        payload = await request.json()
        files = payload.get("files")
        if files is None and payload.get("scenario"):
            try:
                loaded = scenarios.load(str(payload["scenario"]))
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=404, detail="Scenario not found") from exc
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
        if not isinstance(files, dict) or not all(
            isinstance(name, str) and isinstance(text, str) for name, text in files.items()
        ):
            raise HTTPException(status_code=422, detail="files must map names to YAML text")
        try:
            return run_preview_provider(
                provider,
                files,
                item_key,
                PreviewContext(repository_root=repository),
            )
        except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/api/form-choices")
    async def api_form_choices(request: Request):
        import asyncio

        payload = await request.json()
        files = payload.get("files") or {}
        provider = str(payload.get("provider") or "")
        if not isinstance(files, dict) or not all(
            isinstance(name, str) and isinstance(text, str) for name, text in files.items()
        ):
            raise HTTPException(status_code=422, detail="files must map names to YAML text")
        try:
            choices = await asyncio.to_thread(run_choice_provider, provider, files)
        except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"choices": choices}

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
        rows = []
        for index, row in enumerate(readers[stream]()):
            if index < start:
                continue
            if len(rows) >= page_size:
                break
            rows.append(row)
        return {"items": rows, "since_index": start, "next_index": start + len(rows)}

    @app.get("/api/runs/{run_id:path}/views/{view_name}")
    def api_run_view(request: Request, run_id: str, view_name: str):
        record = record_or_404(run_id)
        try:
            return build_view(
                view_or_404(view_name, record.artifact.scenario),
                record.artifact,
                _panel_param_overrides(request.query_params),
            )
        except (KeyError, ValueError) as exc:  # unknown panel in a shipped view
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/runs/{run_id:path}/panels/{panel_name}")
    def api_run_panel(request: Request, run_id: str, panel_name: str):
        record = record_or_404(run_id)
        try:
            panel = get_panel(panel_name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if panel.scope != "run":
            raise HTTPException(status_code=404, detail=f"{panel_name!r} is not a run panel")
        missing = missing_requirements(panel, record.artifact)
        if missing:
            raise HTTPException(status_code=409, detail=f"Run lacks: {', '.join(missing)}")
        params = {
            key: _coerce_param(value)
            for key, value in request.query_params.items()
            if not key.startswith("p.")
        }
        return {
            "name": panel.name,
            "title": panel.title,
            "params": params,
            "controls": [
                {
                    "kind": control.kind,
                    "param": control.param,
                    "label": control.label or control.param.title(),
                    "choices": list(control.choices),
                    "value": params.get(control.param),
                }
                for control in panel.controls
            ],
            "output": output_to_dict(panel().build(record.artifact, params)),
        }

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
        try:
            spec = prepare_launch(
                payload,
                repository_root=repository,
                output_root=root,
                draft_root=studio_state / "launch_configs",
            )
        except ScenarioNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except (ValueError, yaml.YAMLError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        job = jobs.submit(
            kind="run",
            command=spec.command,
            cwd=repository,
            scenario=spec.scenario,
            snapshot=spec.snapshot,
            output_dir=spec.output_dir,
        )
        return job.to_dict()

    @app.post("/api/viewers/{run_id:path}/{backend_type}")
    def api_start_viewer(run_id: str, backend_type: str):
        record = record_or_404(run_id)
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
            return {**existing.to_dict(), "url": f"http://127.0.0.1:{existing.port}"}
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
        return {**job.to_dict(), "url": plan.url}

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

    return app
