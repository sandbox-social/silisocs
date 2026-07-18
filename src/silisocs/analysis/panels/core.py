"""Core run-scope panels: health, activity, probes, agents, recent events."""
# ruff: noqa: D101, D102

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.charts import line_figure, table
from silisocs.analysis.inputs import event_frame, probe_frame
from silisocs.analysis.panel import Figure, Grid, Markdown, Panel, Table, register_panel
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact


@register_panel
class HealthSummaryPanel(Panel):
    name = "health_summary"
    title = "Run health"
    scope = "run"

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Grid:
        assert isinstance(artifact, RunArtifact)
        actions = len(event_frame(artifact))
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
        for event in event_frame(artifact):
            counts[event.label][event.episode] += 1
        cumulative = bool(params.get("cumulative", False))
        series: dict[str, dict[int, int]] = {}
        for label, episode_counts in counts.items():
            values: dict[int, int] = {}
            running = 0
            for episode in sorted(episode_counts):
                value = episode_counts[episode]
                if cumulative:
                    running += value
                    value = running
                values[episode] = value
            series[label] = values
        return line_figure(
            series,
            layout={
                "xaxis": {"title": "Episode", "dtick": 1},
                "yaxis": {"title": "Actions", "rangemode": "tozero"},
                "legend": {"orientation": "h", "y": 1.12},
                "hovermode": "x unified",
            },
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
        for event in probe_frame(artifact):
            response = event.value
            if not isinstance(response, (int, float, str)):
                continue
            try:
                numeric = float(response)
            except ValueError:
                continue
            by_probe[event.probe][event.episode].append(numeric)
        return line_figure(
            {
                probe: {episode: sum(values) / len(values) for episode, values in episodes.items()}
                for probe, episodes in by_probe.items()
            },
            layout={
                "xaxis": {"title": "Episode", "dtick": 1},
                "yaxis": {"title": "Mean response"},
                "hovermode": "x unified",
            },
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
        for event in event_frame(artifact):
            counts[event.actor][event.label] += 1
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
        return table(["agent", "actions", "top_action"], rows)


@register_panel
class RecentEventsPanel(Panel):
    name = "recent_events"
    title = "Recent events"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Table:
        assert isinstance(artifact, RunArtifact)
        limit = max(1, min(int(params.get("limit", 12)), 100))
        events = event_frame(artifact)[-limit:]
        rows = [
            {
                "episode": event.episode,
                "agent": event.actor,
                "action": event.label,
            }
            for event in reversed(tuple(events))
        ]
        return table(["episode", "agent", "action"], rows)
