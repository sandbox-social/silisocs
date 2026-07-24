"""Market run panels: what changed hands, and what it cost.

These read only semantic roles (``market.trade``, ``market.listing``,
``market.stock``) and fields, never a backend name — any backend that declares
those roles gets these panels, and every backend that does not never sees them.
"""
# ruff: noqa: D102

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from silisocs.analysis.charts import bar_figure
from silisocs.analysis.inputs import Event, event_frame
from silisocs.analysis.panel import Control, Figure, Grid, Markdown, Panel, Table, register_panel
from silisocs.design.tokens import action_color
from silisocs.evaluations.run_artifact import RunArtifact, StudyArtifact

_TRADE = "market.trade"
_LISTING = "market.listing"
_STOCK = "market.stock"


def _quantity(event: Event) -> int:
    """A row's traded/produced quantity, defaulting to one unit."""
    value = event.field("market.quantity")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


@register_panel
class MarketActivityPanel(Panel):
    """Volume moved per resource per episode, split by what moved it."""

    name = "market_activity"
    title = "Market activity"
    scope = "run"
    requires = frozenset({"action_events"})
    semantics = frozenset({_TRADE, _LISTING, _STOCK})
    controls = (
        Control(kind="select", param="measure", label="Measure", choices=("volume", "value")),
    )

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Figure:
        assert isinstance(artifact, RunArtifact)
        measure = str(params.get("measure") or "volume")
        per_resource: dict[str, Counter[int]] = defaultdict(Counter)
        episodes: set[int] = set()

        for event in event_frame(artifact):
            if _TRADE not in event.tags:
                continue
            resource = event.field("market.resource")
            if resource in (None, ""):
                continue
            episodes.add(event.episode)
            if measure == "value":
                price = event.field("market.price")
                amount = int(price) if isinstance(price, int | float | str) and price != "" else 0
            else:
                amount = _quantity(event)
            per_resource[str(resource)][event.episode] += int(amount)

        # Shared figure builder: colors are content-hashed per resource name, so
        # a resource keeps its color across runs and episode windows.
        return bar_figure(
            {resource: dict(per_resource[resource]) for resource in sorted(per_resource)},
            mode="stack",
            x_values=sorted(episodes),
            layout={
                "xaxis": {"title": "Episode", "dtick": 1},
                "yaxis": {
                    "title": "Value traded" if measure == "value" else "Units traded",
                    "rangemode": "tozero",
                },
                "legend": {"orientation": "h", "y": 1.12},
                "hovermode": "x unified",
            },
        )


@register_panel
class MarketLedgerPanel(Panel):
    """Per-agent flow: what each participant produced, listed, and traded."""

    name = "market_ledger"
    title = "Market ledger"
    scope = "run"
    requires = frozenset({"action_events"})
    semantics = frozenset({_TRADE, _LISTING, _STOCK})
    controls = (Control(kind="episode_slider", param="episode", label="Episode"),)

    def build(self, artifact: RunArtifact | StudyArtifact, params: dict[str, Any]) -> Grid:
        assert isinstance(artifact, RunArtifact)
        episode = params.get("episode")
        episode = int(episode) if isinstance(episode, int) else None

        by_agent: dict[str, Counter[str]] = defaultdict(Counter)
        label_totals: Counter[str] = Counter()
        counterparties: Counter[tuple[str, str]] = Counter()

        for event in event_frame(artifact):
            roles = set(event.tags) & {_TRADE, _LISTING, _STOCK}
            if not roles:
                continue
            if episode is not None and event.episode != episode:
                continue
            quantity = _quantity(event)
            label_totals[event.label] += quantity
            for role in roles:
                by_agent[event.actor][role] += quantity
            other = event.field("market.counterparty")
            if _TRADE in roles and other not in (None, ""):
                counterparties[(event.actor, str(other))] += quantity

        if not by_agent:
            return Grid([Markdown("No market events in this view.")])

        rows = [
            {
                "agent": agent,
                "produced/consumed": totals.get(_STOCK, 0),
                "listed": totals.get(_LISTING, 0),
                "traded": totals.get(_TRADE, 0),
            }
            for agent, totals in sorted(by_agent.items())
        ]
        flows = [
            {"from": source, "to": target, "units": units}
            for (source, target), units in counterparties.most_common(10)
        ]
        items: list[Any] = [
            Table(columns=["agent", "produced/consumed", "listed", "traded"], rows=rows),
            Figure(
                {
                    "data": [
                        {
                            "type": "bar",
                            "orientation": "h",
                            "x": [label_totals[label] for label in sorted(label_totals)],
                            "y": [label.replace("_", " ") for label in sorted(label_totals)],
                            "marker": {
                                "color": [action_color(label) for label in sorted(label_totals)]
                            },
                        }
                    ],
                    "layout": {
                        "xaxis": {"title": "Units", "rangemode": "tozero"},
                        "yaxis": {"automargin": True},
                    },
                }
            ),
        ]
        if flows:
            items.append(Table(columns=["from", "to", "units"], rows=flows))
        return Grid(items)
