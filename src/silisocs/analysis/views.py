"""Declarative analysis view loading and composition."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml

from silisocs.analysis.panel import (
    Markdown,
    Panel,
    get_panel,
    output_to_dict,
    serialize_controls,
    validate_panel,
)
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


def declared_skip_reason(panel: type[Panel], artifact: RunArtifact | StudyArtifact) -> str:
    """The declaration-level gate alone, or "" when the declarations pass.

    Reads only the manifest (streams, tags, semantic roles) — never event logs —
    so lazy contexts like the exploration capability document can gate panels
    without parsing a run. A panel's data-based ``applicable()`` override is
    deliberately NOT consulted here; :func:`skip_reason` adds it at render time.
    """
    from silisocs.analysis.panels._shared import run_has_tags, run_semantic_roles

    missing = missing_requirements(panel, artifact)
    if missing:
        return f"requires {', '.join(missing)} — not recorded by this run"
    if not isinstance(artifact, RunArtifact):
        return ""
    if panel.needs_tags and not run_has_tags(artifact):
        return "this run's backend declares no action tags"
    if panel.semantics and not (panel.semantics & run_semantic_roles(artifact)):
        needs = ", ".join(sorted(panel.semantics))
        return f"needs {needs} — not declared by this run's backend"
    return ""


def skip_reason(panel: type[Panel], artifact: RunArtifact | StudyArtifact) -> str:
    """Why this panel says nothing about this artifact, or "" when it applies.

    The reason names the gate that actually failed — the panel's declarations say
    which — so the footnote tells you what a run would need rather than a vague
    "unsupported". Builds on :func:`declared_skip_reason` (the lazy tier) and
    adds the panel's ``applicable()`` check, which may read run data.
    """
    reason = declared_skip_reason(panel, artifact)
    if reason:
        return reason
    if panel.applicable(artifact):
        return ""
    if not isinstance(artifact, RunArtifact):
        return "not applicable to this artifact"
    # A panel gating on its own reading of the run's data (see Panel.applicable).
    return "nothing in this run to show"


def view_applies(view: View, artifact: RunArtifact | StudyArtifact) -> bool:
    """Whether a view is worth offering for an artifact — the nav's gate.

    A view is defined by the panels that declare semantics: they are its subject,
    and generic panels alongside them are supporting context. So a view with a
    subject stands or falls with it (no "Network" entry for a market run, no
    "Market" entry for a social one), while a view that is generic throughout
    just needs something to show.
    """
    try:
        panels = [slot.panel_class() for slot in view.panels]
    except (KeyError, TypeError, ValueError):
        # A view referencing an unknown panel is a config error; surface it when
        # the view is opened, not by silently dropping it from the nav.
        return True
    subject = [panel for panel in panels if panel.semantics]
    return any(not skip_reason(panel, artifact) for panel in subject or panels)


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
        # One-off class_path panels get the same declaration checks registered
        # panels do (e.g. a study panel declaring run-only gates fails loud).
        return validate_panel(cls)


@dataclass(frozen=True)
class View:
    """Named composition of panels for one artifact scope."""

    name: str
    title: str
    scope: Literal["run", "study"]
    layout: Literal["grid", "rows"]
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
    "market": {
        "title": "Market",
        "scope": "run",
        "layout": "rows",
        "panels": [
            {"built_in": "market_activity"},
            {"built_in": "market_ledger"},
            {"built_in": "behavior_breakdown"},
            {"built_in": "agent_timeline"},
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
    # 'tabs' was accepted and offered for a long time without a single line of CSS
    # behind it, so it silently rendered as a grid. Rejecting it is the honest
    # answer until a tabbed layout actually exists.
    if layout == "tabs":
        raise ValueError("View layout 'tabs' is not implemented; use grid or rows")
    if not name or scope not in {"run", "study"} or layout not in {"grid", "rows"}:
        raise ValueError("View requires name, scope run|study, and layout grid|rows")
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
    skipped = []
    for slot in view.panels:
        panel = slot.panel_class()
        if panel.scope != view.scope:
            raise ValueError(f"Panel {panel.name!r} has scope {panel.scope}, not {view.scope}")
        # A panel that cannot say anything about this run is not built here. It
        # still carries its requirements: a skipped panel whose only gap is an
        # event stream the run has not yet recorded (``missing``) can be revived
        # live — the Watch shell renders a placeholder for it and refreshes it
        # once that stream grows — while one gated on the backend itself (empty
        # ``missing``) is a permanent skip the shell reports as a footnote.
        reason = skip_reason(panel, artifact)
        if reason:
            skipped.append(
                {
                    "name": panel.name,
                    "title": panel.title,
                    "reason": reason,
                    "requires": sorted(panel.requires),
                    "missing": missing_requirements(panel, artifact),
                }
            )
            continue
        params = {**slot.params, **overrides.get(panel.name, {})}
        # Isolate a failing panel so one bad slot renders an error card rather
        # than 500-ing the whole view/report. Unknown-panel and scope errors
        # above still propagate (they are view-config errors).
        try:
            output: Any = panel().build(artifact, params)
        except Exception as exc:
            output = Markdown(f"Panel {panel.name!r} failed to render: {exc}")
        built.append(
            {
                "name": panel.name,
                "title": panel.title,
                "requires": sorted(panel.requires),
                "controls": serialize_controls(panel, params),
                "output": output_to_dict(output),
            }
        )
    return {
        "name": view.name,
        "title": view.title,
        "scope": view.scope,
        "layout": view.layout,
        "panels": built,
        "skipped": skipped,
    }
