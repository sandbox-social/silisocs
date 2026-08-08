"""Projecting Studio's own state into the shapes its pages and API serve.

These are the only place a run record becomes a JSON body, a portable panel cell
becomes a Studio link, or a workspace source becomes composer context — so the
same run looks the same everywhere it appears.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from silisocs.studio.form_schema import ChoiceContext
from silisocs.studio.routes.lookups import discover_all_runs
from silisocs.studio.studies import evaluation_presets

if TYPE_CHECKING:  # pragma: no cover - typing only
    from silisocs.studio.state import StudioState


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


def choice_context(state: StudioState, source_id: str | None = None) -> ChoiceContext:
    """Build the choice-provider context bound to one workspace source."""
    source = state.workspace.source(source_id)
    return ChoiceContext(
        repository_root=source.path,
        extension_options=lambda kind: state.workspace.extension_options(
            kind,
            preferred_source=source.id,
        ),
    )


def study_composer_catalog(state: StudioState) -> dict[str, Any]:
    """Collect the choices the study composer form offers."""
    return {
        "evaluation_presets": evaluation_presets(),
        "run_choices": [run_json(record) for record in discover_all_runs(state)],
        "scenario_choices": state.workspace.scenarios(),
        "repositories": [source.to_dict() for source in state.workspace.sources],
    }


def _run_href_index(state: StudioState) -> dict[str, str]:
    """Map every resolved run path to its Studio run-page href, across both roots."""
    return {
        record.path.resolve().as_posix(): f"/runs/{record.id}"
        for record in discover_all_runs(state)
    }


def _linkify_cell(value: Any, index: Callable[[], dict[str, str]]) -> Any:
    """Turn one portable ``{"run_path", "text"}`` cell into a link, or leave it alone."""
    if not (isinstance(value, dict) and "run_path" in value):
        return value
    text = str(value.get("text") or "open")
    run_path = str(value.get("run_path") or "")
    href = index().get(Path(run_path).resolve().as_posix()) if run_path else None
    return {"text": text, "href": href} if href else {"text": text}


def _linkify_outputs(output: Any, index: Callable[[], dict[str, str]]) -> None:
    """Rewrite run-reference cells in a panel output tree, in place."""
    if not isinstance(output, dict):
        return
    if output.get("type") == "table":
        output["rows"] = [
            {key: _linkify_cell(cell, index) for key, cell in row.items()}
            for row in output.get("rows", [])
            if isinstance(row, dict)
        ]
    elif output.get("type") == "grid":
        for item in output.get("items", []):
            _linkify_outputs(item, index)


def resolve_run_links(state: StudioState, payload: dict[str, Any]) -> dict[str, Any]:
    """Resolve run-reference table cells into Studio run links, in place.

    Panels emit portable ``{"run_path", "text"}`` cells (see
    docs/analysis_panels.md — "Run reference cells"); only a shell that can
    address run pages turns them into hrefs, and only here. A path no
    discovery root knows degrades to its text. The run index is built
    lazily, on the first reference actually seen.
    """
    index = cache(lambda: _run_href_index(state))
    for panel in payload.get("panels", []):
        _linkify_outputs(panel.get("output"), index)
    _linkify_outputs(payload.get("output"), index)
    return payload
