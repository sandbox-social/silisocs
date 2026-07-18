"""Generic panels over standardized exposure and probe event streams."""
# ruff: noqa: D101, D102

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

from silisocs.analysis.inputs import event_frame, probe_frame
from silisocs.analysis.panel import Control, Figure, Panel, register_panel
from silisocs.analysis.panels._shared import episode_of
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact


def _exposure_size(row: Mapping[str, Any]) -> int:
    """Return an exposure cardinality without assuming a backend payload name."""
    explicit = row.get("count")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return max(0, int(explicit))
    ignored = {
        "episode",
        "event_index",
        "event_type",
        "agent",
        "source_user",
        "backend_type",
        "gm_name",
        "timestamp",
    }
    collections = [
        value for key, value in row.items() if key not in ignored and isinstance(value, list)
    ]
    return sum(len(value) for value in collections) if collections else 1


@register_panel
class ExposureFunnelPanel(Panel):
    name = "exposure_funnel"
    title = "Exposure and action volume"
    scope = "run"
    requires = frozenset({"exposure_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        exposures: Counter[int] = Counter()
        actions: Counter[int] = Counter()
        for row in artifact.exposures:
            exposures[episode_of(row)] += _exposure_size(row)
        for event in event_frame(artifact):
            actions[event.episode] += 1
        episodes = sorted(set(exposures) | set(actions))
        return Figure(
            {
                "data": [
                    {
                        "type": "bar",
                        "name": "Exposures",
                        "x": episodes,
                        "y": [exposures[episode] for episode in episodes],
                    },
                    {
                        "type": "bar",
                        "name": "Actions",
                        "x": episodes,
                        "y": [actions[episode] for episode in episodes],
                    },
                ],
                "layout": {
                    "barmode": "group",
                    "xaxis": {"title": "Episode", "dtick": 1},
                    "yaxis": {"title": "Events", "rangemode": "tozero"},
                },
            }
        )


@register_panel
class ProbeDistributionPanel(Panel):
    name = "probe_distribution"
    title = "Probe response distribution"
    scope = "run"
    requires = frozenset({"probe_events"})
    controls = (
        Control(kind="probe_select", param="probe", label="Probe"),
        Control(kind="episode_slider", param="step", label="Episode"),
    )

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        grouped: dict[str, list[tuple[int, str, Any]]] = defaultdict(list)
        for event in probe_frame(artifact):
            grouped[event.probe].append((event.episode, event.agent, event.value))
        selected = str(params.get("probe") or next(iter(sorted(grouped)), ""))
        requested_step = params.get("step")
        records = [
            (episode, agent, value)
            for episode, agent, value in grouped.get(selected, [])
            if requested_step is None or episode == int(requested_step)
        ]
        numeric = [
            float(value)
            for _, _, value in records
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        ]
        if len(numeric) == len(records) and records:
            data = [
                {
                    "type": "violin",
                    "name": selected,
                    "y": numeric,
                    "text": [agent for _, agent, _ in records],
                    "box": {"visible": True},
                    "points": "all",
                }
            ]
            yaxis = {"title": "Response"}
        else:
            counts = Counter(str(value) for _, _, value in records if value is not None)
            data = [
                {"type": "bar", "name": selected, "x": list(counts), "y": list(counts.values())}
            ]
            yaxis = {"title": "Responses", "rangemode": "tozero"}
        return Figure(
            {
                "data": data,
                "layout": {
                    "xaxis": {"title": selected or "Probe response"},
                    "yaxis": yaxis,
                },
            }
        )
