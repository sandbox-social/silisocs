"""Behavioral run panels: per-episode action mix by open action tag."""
# ruff: noqa: D101, D102

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.charts import bar_figure
from silisocs.analysis.inputs import event_frame
from silisocs.analysis.panel import Figure, Panel, register_panel
from silisocs.analysis.panels._shared import backend_types, semantics_for_backend
from silisocs.design.tokens import GROUP_COLORS, tag_color
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

_CONVENTIONAL_ORDER = tuple(tag for tag in GROUP_COLORS if tag != "other")


@register_panel
class BehaviorBreakdownPanel(Panel):
    name = "behavior_breakdown"
    title = "Behavior breakdown"
    scope = "run"
    requires = frozenset({"action_events"})
    needs_tags = True

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        types = backend_types(artifact)
        backend_type = str(params.get("backend_type") or (types[0] if types else ""))
        fallback = semantics_for_backend(artifact, backend_type)

        counts: dict[str, Counter[int]] = defaultdict(Counter)
        episodes: set[int] = set()
        for event in event_frame(artifact):
            episodes.add(event.episode)
            tags = event.tags or fallback.tags_of(event.label)
            counts[tags[0] if tags else "other"][event.episode] += 1

        x = sorted(episodes)
        custom = sorted(set(counts) - set(_CONVENTIONAL_ORDER) - {"other"})
        order = [
            *(tag for tag in _CONVENTIONAL_ORDER if tag in counts),
            *custom,
            *(("other",) if "other" in counts else ()),
        ]
        return bar_figure(
            {tag: counts[tag] for tag in order},
            mode="stack",
            x_values=x,
            colors={tag: tag_color(tag) for tag in order},
            layout={
                "xaxis": {"title": "Episode", "dtick": 1},
                "yaxis": {"title": "Actions", "rangemode": "tozero"},
            },
        )


@register_panel
class ActionAlignmentPanel(Panel):
    """Compare suggested actions with committed labels when that telemetry exists."""

    name = "action_alignment"
    title = "Action alignment"
    scope = "run"
    requires = frozenset({"action_events"})

    @classmethod
    def applicable(cls, artifact: RunArtifact | StudyArtifact) -> bool:
        """Only runs whose actions carry a suggestion have an alignment to show.

        No semantic role expresses this: suggestions are optional telemetry a
        backend attaches per action, not a property of the backend type — so the
        gate reads the data. Without it this panel is an empty heatmap on every
        run that has no recommender.
        """
        if not isinstance(artifact, RunArtifact):
            return True
        return any(
            event.data.get("suggested_action") not in (None, "") for event in event_frame(artifact)
        )

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        pairs: Counter[tuple[str, str]] = Counter()
        actual: set[str] = set()
        suggested: set[str] = set()
        for event in event_frame(artifact):
            recommendation = event.data.get("suggested_action")
            if recommendation in (None, ""):
                continue
            chosen = event.label
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
