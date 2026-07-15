"""Core run-scope panels: health, activity, probes, agents, recent events."""
# ruff: noqa: D101, D102

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.panel import Figure, Grid, Markdown, Panel, Table, register_panel
from silisocs.analysis.panels._shared import episode_of
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact
from silisocs.visual_tokens import action_color


@register_panel
class HealthSummaryPanel(Panel):
    name = "health_summary"
    title = "Run health"
    scope = "run"

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Grid:
        assert isinstance(artifact, RunArtifact)
        actions = sum(1 for _ in artifact.iter_actions())
        failures = sum(artifact.health.values())
        usage = artifact.llm_usage or {}
        totals = usage.get("totals", {}) if isinstance(usage, dict) else {}
        return Grid(
            [
                Markdown(f"**{artifact.num_agents or 0}**\nAgents"),
                Markdown(f"**{artifact.num_steps or 0}**\nSteps"),
                Markdown(f"**{actions:,}**\nCommitted actions"),
                Markdown(f"**{int(totals.get('total_tokens', 0) or 0):,}**\nTokens"),
                Markdown(f"**{failures}**\nRuntime issues"),
            ]
        )


@register_panel
class ActionTrendsPanel(Panel):
    name = "action_trends"
    title = "Activity over time"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        counts: dict[str, Counter[int]] = defaultdict(Counter)
        for row in artifact.iter_actions():
            counts[str(row.get("label", "unknown"))][episode_of(row)] += 1
        traces = []
        cumulative = bool(params.get("cumulative", False))
        for label, episode_counts in sorted(counts.items()):
            x = sorted(episode_counts)
            y = [episode_counts[value] for value in x]
            if cumulative:
                running = 0
                y = [(running := running + value) for value in y]
            traces.append(
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": label.replace("_", " ").title(),
                    "x": x,
                    "y": y,
                    "line": {"color": action_color(label), "width": 2},
                }
            )
        return Figure(
            {
                "data": traces,
                "layout": {
                    "xaxis": {"title": "Episode", "dtick": 1},
                    "yaxis": {"title": "Actions", "rangemode": "tozero"},
                    "legend": {"orientation": "h", "y": 1.12},
                    "hovermode": "x unified",
                },
            }
        )


@register_panel
class ProbeTrendsPanel(Panel):
    name = "probe_trends"
    title = "Probe responses"
    scope = "run"
    requires = frozenset({"probe_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        by_probe: dict[str, dict[int, list[float]]] = defaultdict(lambda: defaultdict(list))
        for row in artifact.iter_probes():
            response = row.get("response", (row.get("data") or {}).get("probe_return"))
            if not isinstance(response, (int, float, str)):
                continue
            try:
                numeric = float(response)
            except ValueError:
                continue
            by_probe[str(row.get("probe", row.get("label", "Probe")))][episode_of(row)].append(
                numeric
            )
        traces = []
        for probe, episodes in sorted(by_probe.items()):
            x = sorted(episodes)
            traces.append(
                {
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": probe,
                    "x": x,
                    "y": [sum(episodes[e]) / len(episodes[e]) for e in x],
                }
            )
        return Figure(
            {
                "data": traces,
                "layout": {
                    "xaxis": {"title": "Episode", "dtick": 1},
                    "yaxis": {"title": "Mean response"},
                    "hovermode": "x unified",
                },
            }
        )


@register_panel
class AgentInspectorPanel(Panel):
    name = "agent_inspector"
    title = "Agent activity"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Table:
        assert isinstance(artifact, RunArtifact)
        counts: dict[str, Counter[str]] = defaultdict(Counter)
        for row in artifact.iter_actions():
            counts[str(row.get("source_user", "Unknown"))][str(row.get("label", "unknown"))] += 1
        rows = []
        for agent, labels in sorted(
            counts.items(), key=lambda item: (-sum(item[1].values()), item[0])
        ):
            rows.append(
                {
                    "agent": agent,
                    "actions": sum(labels.values()),
                    "top_action": labels.most_common(1)[0][0],
                }
            )
        return Table(columns=["agent", "actions", "top_action"], rows=rows)


@register_panel
class RecentEventsPanel(Panel):
    name = "recent_events"
    title = "Recent events"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Table:
        assert isinstance(artifact, RunArtifact)
        limit = max(1, min(int(params.get("limit", 12)), 100))
        events = list(artifact.iter_actions())[-limit:]
        rows = [
            {
                "episode": episode_of(row),
                "agent": row.get("source_user", "Unknown"),
                "action": row.get("label", "unknown"),
            }
            for row in reversed(events)
        ]
        return Table(columns=["episode", "agent", "action"], rows=rows)
