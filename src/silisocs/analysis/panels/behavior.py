"""Behavioral run panels: per-episode action mix by vocabulary group."""
# ruff: noqa: D101, D102

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.panel import Figure, Panel, register_panel
from silisocs.analysis.panels._shared import backend_types, episode_of, vocabulary_for_event
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact
from silisocs.evaluations.vocabulary import ActionVocabulary, vocabulary_for

# Fixed group order and colors; labels outside every group land in "other".
_GROUP_COLORS: dict[str, str] = {
    "creates_content": "#277da1",
    "endorses": "#43aa8b",
    "negative": "#d1495b",
    "social_graph": "#7b61a8",
    "reads": "#8a979d",
    "other": "#b9c2c6",
}
_GROUP_ORDER = tuple(_GROUP_COLORS)


def _group_for(label: str, vocabulary: ActionVocabulary) -> str:
    """Assign a logged action label to its vocabulary group (else ``other``)."""
    if label in vocabulary.creates_content:
        return "creates_content"
    if label in vocabulary.endorses:
        return "endorses"
    if label in vocabulary.negative:
        return "negative"
    if label in vocabulary.social_graph:
        return "social_graph"
    if label in vocabulary.reads:
        return "reads"
    return "other"


@register_panel
class BehaviorBreakdownPanel(Panel):
    name = "behavior_breakdown"
    title = "Behavior breakdown"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        types = backend_types(artifact)
        backend_type = str(params.get("backend_type") or (types[0] if types else ""))
        fallback_vocabulary = vocabulary_for(backend_type)

        counts: dict[str, Counter[int]] = defaultdict(Counter)
        episodes: set[int] = set()
        for row in artifact.iter_actions():
            episode = episode_of(row)
            episodes.add(episode)
            vocabulary = vocabulary_for_event(row, artifact)
            if not vocabulary.interactions and backend_type:
                vocabulary = fallback_vocabulary
            counts[_group_for(str(row.get("label", "unknown")), vocabulary)][episode] += 1

        x = sorted(episodes)
        traces = []
        for group in _GROUP_ORDER:
            if group not in counts:
                continue
            per_episode = counts[group]
            traces.append(
                {
                    "type": "bar",
                    "name": group.replace("_", " ").title(),
                    "x": x,
                    "y": [per_episode.get(episode, 0) for episode in x],
                    "marker": {"color": _GROUP_COLORS[group]},
                }
            )
        return Figure(
            {
                "data": traces,
                "layout": {
                    "barmode": "stack",
                    "xaxis": {"title": "Episode", "dtick": 1},
                    "yaxis": {"title": "Actions", "rangemode": "tozero"},
                },
            }
        )


@register_panel
class ActionAlignmentPanel(Panel):
    """Compare suggested actions with committed labels when that telemetry exists."""

    name = "action_alignment"
    title = "Action alignment"
    scope = "run"
    requires = frozenset({"action_events"})

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        pairs: Counter[tuple[str, str]] = Counter()
        actual: set[str] = set()
        suggested: set[str] = set()
        for row in artifact.iter_actions():
            data = row.get("data")
            recommendation = data.get("suggested_action") if isinstance(data, dict) else None
            if recommendation in (None, ""):
                continue
            chosen = str(row.get("label") or "unknown")
            expected = str(recommendation)
            actual.add(chosen)
            suggested.add(expected)
            pairs[(chosen, expected)] += 1
        x = sorted(suggested)
        y = sorted(actual)
        return Figure(
            {
                "data": [
                    {
                        "type": "heatmap",
                        "x": x,
                        "y": y,
                        "z": [[pairs[(chosen, expected)] for expected in x] for chosen in y],
                        "colorscale": "Teal",
                        "hovertemplate": (
                            "Suggested %{x}<br>Chosen %{y}<br>Count %{z}<extra></extra>"
                        ),
                    }
                ],
                "layout": {
                    "xaxis": {"title": "Suggested action"},
                    "yaxis": {"title": "Chosen action"},
                },
            }
        )
