"""Declarative analysis view loading and composition."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from silisocs.analysis.panel import Markdown, Panel, get_panel, output_to_dict
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

# Requirement names build_view can check against a run (Panel.requires may also
# carry richer hints — e.g. a vocabulary name — that the shell ignores here).
_FEATURE_PROBES = {
    "action_events": RunArtifact.action_event_files,
    "exposure_events": RunArtifact.exposure_event_files,
    "probe_events": RunArtifact.probe_event_files,
    "harness_events": RunArtifact.harness_event_files,
}


def missing_requirements(panel: type[Panel], artifact: RunArtifact | StudyArtifact) -> list[str]:
    """Return the checkable panel requirements the artifact cannot satisfy."""
    if not isinstance(artifact, RunArtifact):
        return []
    return sorted(
        name
        for name in panel.requires
        if name in _FEATURE_PROBES and not _FEATURE_PROBES[name](artifact)
    )


@dataclass(frozen=True)
class PanelSlot:
    """One built-in or class-path panel declaration."""

    built_in: str | None = None
    class_path: str | None = None
    params: dict[str, Any] = field(default_factory=dict)

    def panel_class(self) -> type[Panel]:
        """Resolve the declaration to a Panel class."""
        if bool(self.built_in) == bool(self.class_path):
            raise ValueError("Panel slot requires exactly one of built_in or class_path")
        if self.built_in:
            return get_panel(self.built_in)
        module_name, class_name = str(self.class_path).rsplit(".", 1)
        cls = getattr(importlib.import_module(module_name), class_name)
        if not isinstance(cls, type) or not issubclass(cls, Panel):
            raise TypeError(f"{self.class_path} is not a Panel class")
        return cls


@dataclass(frozen=True)
class View:
    """Named composition of panels for one artifact scope."""

    name: str
    title: str
    scope: Literal["run", "study"]
    layout: Literal["grid", "tabs", "rows"]
    panels: tuple[PanelSlot, ...]


BUILTIN_VIEWS: dict[str, dict[str, Any]] = {
    "overview": {
        "title": "Run Overview",
        "scope": "run",
        "layout": "grid",
        "panels": [
            {"built_in": "health_summary"},
            {"built_in": "action_trends"},
            {"built_in": "agent_inspector"},
            {"built_in": "recent_events"},
            {"built_in": "exposure_funnel"},
        ],
    },
    "network": {
        "title": "Interaction Network",
        "scope": "run",
        "layout": "rows",
        "panels": [{"built_in": "interaction_network"}, {"built_in": "agent_timeline"}],
    },
    "content": {
        "title": "Content",
        "scope": "run",
        "layout": "rows",
        "panels": [
            {"built_in": "content_feed"},
            {"built_in": "recent_events"},
            {"built_in": "behavior_breakdown"},
            {"built_in": "action_alignment"},
        ],
    },
    "probes": {
        "title": "Probe Analysis",
        "scope": "run",
        "layout": "rows",
        "panels": [{"built_in": "probe_trends"}, {"built_in": "probe_distribution"}],
    },
    "cost": {
        "title": "Usage & Cost",
        "scope": "run",
        "layout": "grid",
        "panels": [{"built_in": "token_usage"}, {"built_in": "health_summary"}],
    },
    "comparison": {
        "title": "Study Comparison",
        "scope": "study",
        "layout": "grid",
        "panels": [{"built_in": "condition_comparison"}],
    },
    "hypotheses": {
        "title": "Hypotheses",
        "scope": "study",
        "layout": "rows",
        "panels": [
            {"built_in": "hypothesis_board"},
            {"built_in": "per_agent_distributions"},
        ],
    },
    "progress": {
        "title": "Study Progress",
        "scope": "study",
        "layout": "rows",
        "panels": [{"built_in": "study_progress"}],
    },
}


def parse_view(data: dict[str, Any]) -> View:
    """Validate and parse a view mapping."""
    raw = data.get("view", data)
    if not isinstance(raw, dict):
        raise ValueError("View document must contain a mapping")
    name = str(raw.get("name", "")).strip()
    scope = raw.get("scope")
    layout = raw.get("layout", "grid")
    if not name or scope not in {"run", "study"} or layout not in {"grid", "tabs", "rows"}:
        raise ValueError("View requires name, scope run|study, and layout grid|tabs|rows")
    panels = raw.get("panels", [])
    if not isinstance(panels, list) or not panels:
        raise ValueError(f"View {name!r} requires at least one panel")
    slots = tuple(
        PanelSlot(
            built_in=item.get("built_in"),
            class_path=item.get("class_path"),
            params=dict(item.get("params") or {}),
        )
        for item in panels
        if isinstance(item, dict)
    )
    if len(slots) != len(panels):
        raise ValueError(f"View {name!r} contains an invalid panel slot")
    return View(name, str(raw.get("title") or name.replace("_", " ").title()), scope, layout, slots)


def load_view(source: str | Path) -> View:
    """Load a built-in view name or YAML view document."""
    if str(source) in BUILTIN_VIEWS:
        return parse_view({"name": str(source), **BUILTIN_VIEWS[str(source)]})
    path = Path(source)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return parse_view(data)


def scenario_view_files(scenario: str, root: str | Path = "scenarios") -> dict[str, Path]:
    """Views a scenario ships next to its config (``scenarios/<name>/conf/views/*.yaml``).

    Keyed by view file stem; scenario names containing path separators are
    rejected so callers can pass request-derived scenario names safely.
    """
    if not scenario or any(sep in scenario for sep in ("/", "\\", "..")):
        return {}
    views_dir = Path(root) / scenario / "conf" / "views"
    if not views_dir.is_dir():
        return {}
    return {path.stem: path for path in sorted(views_dir.glob("*.yaml"))}


def build_view(
    view: View,
    artifact: RunArtifact | StudyArtifact,
    param_overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build every applicable panel in a view into API-ready JSON.

    ``param_overrides`` maps panel name -> params merged over the slot's own
    params — the seam shell controls (episode scrubber, agent picker) use.
    """
    artifact_scope = "run" if isinstance(artifact, RunArtifact) else "study"
    if artifact_scope != view.scope:
        raise ValueError(f"View {view.name!r} expects {view.scope}, got {artifact_scope}")
    overrides = param_overrides or {}
    built = []
    for slot in view.panels:
        panel = slot.panel_class()
        if panel.scope != view.scope:
            raise ValueError(f"Panel {panel.name!r} has scope {panel.scope}, not {view.scope}")
        params = {**slot.params, **overrides.get(panel.name, {})}
        missing = missing_requirements(panel, artifact)
        if missing:
            output: Any = Markdown(f"Requires {', '.join(missing)} — not recorded by this run.")
        else:
            output = panel().build(artifact, params)
        built.append(
            {
                "name": panel.name,
                "title": panel.title,
                "requires": sorted(panel.requires),
                "missing": missing,
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
                "output": output_to_dict(output),
            }
        )
    return {
        "name": view.name,
        "title": view.title,
        "scope": view.scope,
        "layout": view.layout,
        "panels": built,
    }
