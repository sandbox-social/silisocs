"""Extensible, artifact-backed analysis panels and views."""

from silisocs.analysis.charts import bar_figure, line_figure, table
from silisocs.analysis.inputs import Event, EventFrame, ProbeEvent, event_frame, probe_frame
from silisocs.analysis.panel import (
    Control,
    Figure,
    Grid,
    Html,
    Markdown,
    Panel,
    Table,
    get_panel,
    list_panels,
    register_panel,
)

__all__ = [
    "Control",
    "Event",
    "EventFrame",
    "Figure",
    "Grid",
    "Html",
    "Markdown",
    "Panel",
    "ProbeEvent",
    "Table",
    "bar_figure",
    "event_frame",
    "get_panel",
    "line_figure",
    "list_panels",
    "probe_frame",
    "register_panel",
    "table",
]
