"""Import-level smoke tests for the two dashboard UI entry points.

The Streamlit scenario builder (``dashboard/launch_app.py``) and the Plotly Dash
analysis app (``evaluations/analysis/dashboard/main.py``) had no import coverage,
so a NameError or bad reference in the UI shell (e.g. a stale config key, or a
callback pointing at a removed component) only surfaced at launch. These tests
import each UI — importing ``launch_app`` executes the whole Streamlit script in
bare mode, and ``create_app`` builds the Dash layout and registers every callback
— so wiring bugs fail in CI instead. Each is skipped when its optional UI extra
(``dashboard`` / ``analysis``) is not installed.
"""

from __future__ import annotations

import importlib

import pytest


def test_scenario_dashboard_imports() -> None:
    """The Streamlit scenario builder imports and runs top-to-bottom in bare mode."""
    pytest.importorskip("streamlit")
    module = importlib.import_module("silisocs.dashboard.launch_app")
    # The tool-calling fix removed the invalid action mode; assert it stays gone so
    # the runner can never be handed sim.action_mode='tool_calling'.
    assert module._ACTION_MODES == ["custom", "generic"]
    assert "none" in module._TOOL_CALLING_MODE_OPTIONS


def test_analysis_dashboard_builds_app() -> None:
    """The Dash analysis app constructs its layout and registers callbacks."""
    pytest.importorskip("dash")
    pytest.importorskip("dash_cytoscape")
    pytest.importorskip("plotly")
    main = importlib.import_module("silisocs.evaluations.analysis.dashboard.main")
    app = main.create_app(None)
    # Building the app validates that no callback Output references a component the
    # layout no longer contains (the removed heatmap panel would trip this).
    assert app.layout is not None
