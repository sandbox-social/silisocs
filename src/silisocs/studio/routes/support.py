"""Lookups and projections the Studio routers share.

These are the helpers ``create_app`` used to define as closures. Everything they
used to capture now lives on ``app.state`` (populated once in ``create_app``),
which each helper takes as its first argument — routers pass
``request.app.state``. No module-level singletons: one process can host several
Studio apps with different roots.
"""
# ruff: noqa: C901
#
# C901: `resolve_run_links` and `_shipped_panel` are moved verbatim from the
# create_app closure, which carried the same suppression.

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import HTTPException

from silisocs.analysis.panel import get_panel
from silisocs.analysis.views import BUILTIN_VIEWS, load_view, scenario_view_files, view_applies
from silisocs.studio.catalog import discover_runs, find_run
from silisocs.studio.studies import evaluation_presets

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import Request

    # Runtime-imported inside `choice_context`: the composer layer pulls the
    # whole engine import tree, which no other helper here needs.
    from silisocs.studio.forms import ChoiceContext

# Study replicate runs live under the studies root (experiments/studies/
# <id>/runs/...), which is usually outside the output root. They are runs
# like any other, so they join the catalog under a second discovery root
# with a stable id prefix — that is what makes a study board row's run
# reference addressable as a run page at all.
STUDY_RUN_PREFIX = "studies/"


def coerce_param(value: str) -> Any:
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


def panel_param_overrides(query_params: Any) -> dict[str, dict[str, Any]]:
    """Parse ``p.<panel>.<param>=value`` query args into build_view overrides."""
    overrides: dict[str, dict[str, Any]] = {}
    for key, value in query_params.items():
        if not key.startswith("p."):
            continue
        parts = key.split(".", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            overrides.setdefault(parts[1], {})[parts[2]] = coerce_param(value)
    return overrides


def panel_params(query_params: Any) -> dict[str, Any]:
    """Read a panel endpoint's own params: every query arg that is not a ``p.`` override."""
    return {
        key: coerce_param(value) for key, value in query_params.items() if not key.startswith("p.")
    }


def choice_context(state: Any, source_id: str | None = None) -> ChoiceContext:
    """Build the choice-provider context bound to one workspace source."""
    from silisocs.studio.forms import ChoiceContext  # noqa: PLC0415

    source = state.workspace.source(source_id)
    return ChoiceContext(
        repository_root=source.path,
        extension_options=lambda kind: state.workspace.extension_options(
            kind,
            preferred_source=source.id,
        ),
    )


def project_environment(state: Any, source_id: str | None = None) -> dict[str, str]:
    """Build the PYTHONPATH a launched subprocess needs to import every source."""
    workspace = state.workspace
    selected = workspace.source(source_id)
    ordered = (selected, *(item for item in workspace.sources if item.id != selected.id))
    paths: list[str] = []
    for source in ordered:
        paths.extend(str(path) for path in (source.path / "src", source.path) if path.is_dir())
    inherited = os.environ.get("PYTHONPATH")
    if inherited:
        paths.append(inherited)
    return {"PYTHONPATH": os.pathsep.join(dict.fromkeys(paths))}


def studies_root_is_separate(state: Any) -> bool:
    """Whether the studies root sits outside the output root."""
    return not state.studies.root.resolve().is_relative_to(state.output_root)


def discover_all_runs(state: Any) -> list[Any]:
    """Discover runs across both roots: the output root and the studies root."""
    records = list(discover_runs(state.output_root))
    if studies_root_is_separate(state):
        # An output-root run already owning an id wins (same precedence as
        # record_or_404), so a literal <root>/studies/... run never coexists
        # with a study replicate under one duplicated id.
        taken = {record.id for record in records}
        records.extend(
            record
            for record in (
                replace(record, id=f"{STUDY_RUN_PREFIX}{record.id}")
                for record in discover_runs(state.studies.root)
            )
            if record.id not in taken
        )
        records.sort(key=lambda record: record.modified, reverse=True)
    return records


def record_or_404(state: Any, run_id: str) -> Any:
    """Resolve a run id to its catalog record, or raise 404."""
    # Output-root runs win the namespace; the studies/ prefix is only
    # consulted when no output-root run matches.
    try:
        return find_run(state.output_root, run_id)
    except KeyError:
        pass
    if run_id.startswith(STUDY_RUN_PREFIX) and studies_root_is_separate(state):
        try:
            record = find_run(state.studies.root, run_id[len(STUDY_RUN_PREFIX) :])
            return replace(record, id=run_id)
        except KeyError:
            pass
    raise HTTPException(status_code=404, detail="Run not found")


def study_or_404(state: Any, study_id: str, **kwargs: Any) -> dict[str, Any]:
    """Load a study by id, or raise 404."""
    try:
        return state.studies.load(study_id, **kwargs)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc


def scenario_or_404(state: Any, scenario_name: str, source: str = "workspace") -> dict[str, Any]:
    """Load a scenario from one workspace source, or raise 404."""
    try:
        return state.workspace.scenario_repository(source).load(scenario_name)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Scenario not found") from exc


def require_file_mapping(files: Any) -> dict[str, str]:
    """Return the payload's file mapping, or raise 422 when it is not one."""
    if not isinstance(files, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in files.items()
    ):
        raise HTTPException(status_code=422, detail="files must map relative names to YAML text")
    return files


def view_or_404(state: Any, view_name: str, scenario: str | None = None) -> Any:
    """Load a view by name from the built-ins plus a scenario's shipped views."""
    # The HTTP surface serves built-in views plus the run's scenario-shipped
    # views (an enumerated, repo-owned file set) — never a free-form path,
    # which would let a request read (and class_path-import) arbitrary files.
    if view_name in BUILTIN_VIEWS:
        return load_view(view_name)
    shipped: dict[str, Path] = {}
    if scenario:
        for source in state.workspace.sources:
            shipped.update(scenario_view_files(scenario, source.scenario_root))
    if view_name in shipped:
        return load_view(shipped[view_name])
    raise HTTPException(status_code=404, detail=f"Unknown view {view_name!r}")


def run_view_names(state: Any, artifact: Any) -> list[str]:
    """List the analysis views worth offering for a run.

    Views whose subject the run's backend cannot feed are dropped, so a
    market run has no Network tab and a social run has no Market tab.
    """
    scenario = artifact.scenario
    candidates = [name for name, spec in BUILTIN_VIEWS.items() if spec["scope"] == "run"]
    candidates.extend(scenario_view_files(scenario, state.scenarios.root) if scenario else ())
    names = []
    for name in candidates:
        try:
            view = view_or_404(state, name, scenario)
        except (HTTPException, ValueError, OSError, yaml.YAMLError):
            # A scenario-shipped views/*.yaml can be malformed; skip that one
            # candidate rather than 500 every tab of the run page.
            continue
        if view_applies(view, artifact):
            names.append(name)
    return names


def study_view_or_404(state: Any, view_name: str, study: dict[str, Any]) -> Any:
    """Resolve a study-scope view: built-ins plus the study's scenarios' shipped views.

    A study is scoped to the scenarios its definition declares, so a
    scenario may ship a study-scope view (``conf/views/*.yaml`` with
    ``scope: study``) and every study using that scenario can select it —
    the same enumerated, repo-owned allowlist the run routes use. A view
    that resolves but is run-scope is a 404, never a scope error mid-build.
    """
    wrong_scope = False
    for scenario in (None, *study_scenario_names(study)):
        try:
            view = view_or_404(state, view_name, scenario)
        except HTTPException:
            continue
        # A same-named run-scope view in one scenario must not shadow a
        # study-scope view of that name shipped by another — keep looking.
        if view.scope != "study":
            wrong_scope = True
            continue
        return view
    detail = f"{view_name!r} is not a study view" if wrong_scope else f"Unknown view {view_name!r}"
    raise HTTPException(status_code=404, detail=detail)


def study_scenario_names(study: dict[str, Any]) -> list[str]:
    """List the scenario names a study definition declares."""
    meta = (study.get("definition") or {}).get("study") or {}
    return [name for name in (meta.get("scenarios") or []) if isinstance(name, str)]


def _shipped_panel(state: Any, panel_name: str, scope: str, scenario_names: list[str]) -> Any:
    """Find a view-slot ``class_path`` panel by name in shipped view files.

    One-off panels (referenced only by ``class_path`` in a view, never
    registered) still need to answer the by-name panel endpoints — that is
    what live refresh calls — so resolution falls back to scanning the same
    enumerated view files ``view_or_404`` trusts. Never a free-form import.
    """
    seen: set[str] = set()
    for scenario in scenario_names:
        for source in state.workspace.sources:
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


def panel_or_404(state: Any, panel_name: str, scope: str, scenario_names: list[str]) -> Any:
    """Resolve a panel by name at one scope, or raise 404."""
    try:
        panel = get_panel(panel_name)
    except KeyError as exc:
        panel = _shipped_panel(state, panel_name, scope, scenario_names)
        if panel is None:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    if panel.scope != scope:
        raise HTTPException(status_code=404, detail=f"{panel_name!r} is not a {scope} panel")
    return panel


def _run_href_index(state: Any) -> dict[str, str]:
    """Map every resolved run path to its Studio run-page href, across both roots."""
    return {
        record.path.resolve().as_posix(): f"/runs/{record.id}"
        for record in discover_all_runs(state)
    }


def resolve_run_links(state: Any, payload: dict[str, Any]) -> dict[str, Any]:
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
            hrefs = _run_href_index(state)
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


def run_json(record: Any) -> dict[str, Any]:
    """Project a run record into the JSON shape the API and composer serve."""
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


def study_composer_catalog(state: Any) -> dict[str, Any]:
    """Collect the choices the study composer form offers."""
    return {
        "evaluation_presets": evaluation_presets(),
        "run_choices": [run_json(record) for record in discover_all_runs(state)],
        "scenario_choices": state.workspace.scenarios(),
        "repositories": [source.to_dict() for source in state.workspace.sources],
    }


def compare_run_ids(request: Request) -> list[str]:
    """Parse the ``runs`` query args (repeated or comma-joined) into run ids."""
    ids = list(request.query_params.getlist("runs"))
    if len(ids) == 1 and "," in ids[0]:
        ids = ids[0].split(",")
    return [run_id.strip() for run_id in ids if run_id.strip()]
