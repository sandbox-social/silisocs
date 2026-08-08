"""Resolving an id in a URL to the thing it names, or raising 404.

Runs, studies, scenarios, views, and panels each have their own allowlist and
their own reason for saying "not found"; every route that addresses one goes
through the matching helper here so those rules are stated once.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from fastapi import HTTPException

from silisocs.analysis.panel import get_panel
from silisocs.analysis.views import BUILTIN_VIEWS, load_view, scenario_view_files, view_applies
from silisocs.studio.catalog import discover_runs, find_run

if TYPE_CHECKING:  # pragma: no cover - typing only
    from silisocs.studio.state import StudioState

# Study replicate runs live under the studies root (experiments/studies/
# <id>/runs/...), which is usually outside the output root. They are runs
# like any other, so they join the catalog under a second discovery root
# with a stable id prefix — that is what makes a study board row's run
# reference addressable as a run page at all.
STUDY_RUN_PREFIX = "studies/"


def studies_root_is_separate(state: StudioState) -> bool:
    """Whether the studies root sits outside the output root."""
    return not state.studies.root.resolve().is_relative_to(state.output_root)


def discover_all_runs(state: StudioState) -> list[Any]:
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


def record_or_404(state: StudioState, run_id: str) -> Any:
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


def study_or_404(state: StudioState, study_id: str, **kwargs: Any) -> dict[str, Any]:
    """Load a study by id, or raise 404."""
    try:
        return state.studies.load(study_id, **kwargs)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Study not found") from exc


def study_scenario_names(study: dict[str, Any]) -> list[str]:
    """List the scenario names a study definition declares."""
    meta = (study.get("definition") or {}).get("study") or {}
    return [name for name in (meta.get("scenarios") or []) if isinstance(name, str)]


def scenario_or_404(
    state: StudioState, scenario_name: str, source: str = "workspace"
) -> dict[str, Any]:
    """Load a scenario from one workspace source, or raise 404 / 422.

    A scenario whose YAML does not parse (or whose bytes are not UTF-8) EXISTS —
    answering "not found" would send the author looking for the wrong problem,
    and letting the error escape 500s the very editor page they would fix it on.
    So a parse/decode failure is a 422 carrying the parser's own message, which
    names the file position.
    """
    try:
        return state.workspace.scenario_repository(source).load(scenario_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Scenario not found") from exc
    # UnicodeDecodeError is a ValueError, so it is caught before the name check.
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=422, detail=f"Scenario {scenario_name!r} could not be read: {exc}"
        ) from exc
    except ValueError as exc:  # an unsafe scenario name addresses nothing
        raise HTTPException(status_code=404, detail="Scenario not found") from exc


def view_or_404(state: StudioState, view_name: str, scenario: str | None = None) -> Any:
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


def run_view_names(state: StudioState, artifact: Any) -> list[str]:
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


def study_view_or_404(state: StudioState, view_name: str, study: dict[str, Any]) -> Any:
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


def _shipped_panel_classes(
    state: StudioState, scope: str, scenario_names: list[str]
) -> Iterator[Any]:
    """Yield the view-slot ``class_path`` panel classes shipped by given scenarios.

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
                        yield slot.panel_class()
                    except (ImportError, AttributeError, TypeError, ValueError):
                        continue


def panel_or_404(state: StudioState, panel_name: str, scope: str, scenario_names: list[str]) -> Any:
    """Resolve a panel by name at one scope, or raise 404."""
    try:
        panel = get_panel(panel_name)
    except KeyError as exc:
        shipped = next(
            (
                cls
                for cls in _shipped_panel_classes(state, scope, scenario_names)
                if cls.name == panel_name
            ),
            None,
        )
        if shipped is None:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        panel = shipped
    if panel.scope != scope:
        raise HTTPException(status_code=404, detail=f"{panel_name!r} is not a {scope} panel")
    return panel
