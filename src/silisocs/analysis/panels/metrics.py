"""Run-scope metrics panels: model token usage and (when priced) cost."""

from __future__ import annotations

from typing import Any

from silisocs.analysis.panel import Figure, Grid, Markdown, Panel, PanelOutput, register_panel
from silisocs.design.tokens import CATEGORICAL_COLORS
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

# Cost is surfaced ONLY when the run already priced its usage — no invented rates.
# Telemetry writes ``estimated_cost_usd`` at the ``llm_usage`` TOP level (see
# runtime/telemetry/engine_metrics.py ``collect_usage_summary``), so ``_cost`` is
# called on the top-level usage dict, not its ``totals`` sub-dict.
_COST_KEYS = ("estimated_cost_usd", "cost", "cost_usd", "estimated_cost", "total_cost")


def _cost(data: dict[str, Any]) -> float | None:
    for key in _COST_KEYS:
        value = data.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _per_model(usage: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Normalize manifest ``per_model`` entries to ``(name, stats)`` pairs."""
    per_model = usage.get("per_model")
    if not isinstance(per_model, list):
        return []
    return [
        (str(entry.get("model", "model")), entry) for entry in per_model if isinstance(entry, dict)
    ]


@register_panel
class TokenUsagePanel(Panel):
    name = "token_usage"
    title = "Model usage & cost"
    scope = "run"

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Grid | Figure:
        assert isinstance(artifact, RunArtifact)
        usage = artifact.llm_usage
        if not usage:
            return Grid([Markdown("**—**\nNo usage recorded")])

        totals = usage.get("totals")
        totals = totals if isinstance(totals, dict) else {}

        models = _per_model(usage)
        if models:
            names = [name for name, _ in models]
            layout: dict[str, Any] = {
                "barmode": "group",
                "xaxis": {"title": "Model"},
                "yaxis": {"title": "Tokens", "rangemode": "tozero"},
            }
            cost = _cost(usage)
            if cost is not None:
                layout["annotations"] = [
                    {
                        "text": f"Estimated cost ${cost:,.2f}",
                        "showarrow": False,
                        "xref": "paper",
                        "yref": "paper",
                        "x": 1.0,
                        "y": 1.12,
                    }
                ]
            return Figure(
                {
                    "data": [
                        {
                            "type": "bar",
                            "name": "Prompt tokens",
                            "x": names,
                            "y": [int(stats.get("prompt_tokens", 0) or 0) for _, stats in models],
                            "marker": {"color": CATEGORICAL_COLORS[0]},
                        },
                        {
                            "type": "bar",
                            "name": "Completion tokens",
                            "x": names,
                            "y": [
                                int(stats.get("completion_tokens", 0) or 0) for _, stats in models
                            ],
                            "marker": {"color": CATEGORICAL_COLORS[1]},
                        },
                    ],
                    "layout": layout,
                }
            )

        tiles: list[PanelOutput] = [
            Markdown(f"**{int(totals.get('total_tokens', 0) or 0):,}**\nTotal tokens"),
            Markdown(f"**{int(totals.get('prompt_tokens', 0) or 0):,}**\nPrompt tokens"),
            Markdown(f"**{int(totals.get('completion_tokens', 0) or 0):,}**\nCompletion tokens"),
        ]
        cost = _cost(usage)
        if cost is not None:
            tiles.append(Markdown(f"**${cost:,.2f}**\nEstimated cost"))
        return Grid(tiles)
