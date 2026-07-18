"""Compact builders for ordinary panel charts and tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from silisocs.analysis.panel import Figure, Table
from silisocs.design.tokens import action_color


def _display_name(value: str) -> str:
    return value.replace("_", " ").replace(".", " ").title()


def line_figure(
    series: Mapping[str, Mapping[Any, int | float]],
    *,
    x_title: str = "",
    y_title: str = "",
    colors: Mapping[str, str] | None = None,
    layout: Mapping[str, Any] | None = None,
) -> Figure:
    """Build a line figure from ``{series: {x: y}}``."""
    traces = [
        {
            "type": "scatter",
            "mode": "lines+markers",
            "name": _display_name(name),
            "x": sorted(values),
            "y": [values[x] for x in sorted(values)],
            "line": {"color": (colors or {}).get(name, action_color(name)), "width": 2},
        }
        for name, values in sorted(series.items())
    ]
    figure_layout: dict[str, Any] = {
        "xaxis": {"title": x_title},
        "yaxis": {"title": y_title},
    }
    figure_layout.update(dict(layout or {}))
    return Figure({"data": traces, "layout": figure_layout})


def bar_figure(
    series: Mapping[str, Mapping[Any, int | float]],
    *,
    mode: str = "group",
    x_title: str = "",
    y_title: str = "",
    colors: Mapping[str, str] | None = None,
    x_values: Sequence[Any] | None = None,
    layout: Mapping[str, Any] | None = None,
) -> Figure:
    """Build a grouped or stacked bar figure from ``{series: {x: y}}``."""
    if mode not in {"group", "stack"}:
        raise ValueError("bar_figure mode must be 'group' or 'stack'")
    traces = []
    for name, values in series.items():
        x = list(x_values) if x_values is not None else sorted(values)
        traces.append(
            {
                "type": "bar",
                "name": _display_name(name),
                "x": x,
                "y": [values.get(value, 0) for value in x],
                "marker": {"color": (colors or {}).get(name, action_color(name))},
            }
        )
    figure_layout: dict[str, Any] = {
        "barmode": mode,
        "xaxis": {"title": x_title},
        "yaxis": {"title": y_title},
    }
    figure_layout.update(dict(layout or {}))
    return Figure({"data": traces, "layout": figure_layout})


def table(columns: Sequence[str | Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> Table:
    """Build the portable table output used by Studio and static reports."""
    normalized = [dict(column) if isinstance(column, Mapping) else column for column in columns]
    return Table(columns=normalized, rows=[dict(row) for row in rows])
