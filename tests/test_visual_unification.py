"""Regression checks for the shared visual identity across product surfaces."""
# ruff: noqa: D103

from __future__ import annotations

import tomllib
from pathlib import Path

from silisocs.analysis.report import _REPORT_CSS
from silisocs.design.css import viewer_stylesheet

ROOT = Path(__file__).parents[1]
VIEWERS = (
    ROOT
    / "src/silisocs/environments/backends/twitter_like/visualizer/templates/index.html",
    ROOT
    / "src/silisocs/environments/backends/reddit_like/visualizer/templates/index.html",
)


def test_backend_viewers_use_the_shared_identity_without_a_cdn() -> None:
    for path in VIEWERS:
        template = path.read_text(encoding="utf-8")
        assert 'href="/assets/design.css"' in template
        assert "viewer-brand-mark" in template
        assert "Silisocs" in template
        assert "silisocs-theme" in template
        assert "cdn.tailwindcss.com" not in template
        assert "tailwind.config" not in template


def test_shared_viewer_stylesheet_contains_tokens_and_responsive_shell() -> None:
    stylesheet = viewer_stylesheet()
    assert "--accent:#0d9488" in stylesheet
    assert "--categorical-7:" in stylesheet
    assert ".viewer-brand{" in stylesheet
    assert ".viewer-mobile-brand" in stylesheet
    assert "@media(max-width:1023px)" in stylesheet


def test_exported_reports_consume_design_variables() -> None:
    assert _REPORT_CSS.startswith(":root{")
    assert "background:var(--canvas)" in _REPORT_CSS
    assert "color:var(--ink)" in _REPORT_CSS
    assert "font:14px var(--font)" in _REPORT_CSS


def test_studio_is_the_single_visual_install_surface() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = project["project"]["optional-dependencies"]
    assert "studio" in extras
    assert "viz" not in extras
    assert "studio" in extras["all"][0]
    assert "viz" not in extras["all"][0]


def test_active_docs_do_not_reference_retired_dashboard_entrypoints() -> None:
    nav = (ROOT / "properdocs.yml").read_text(encoding="utf-8")
    scenario_docs = (ROOT / "agent_docs/scenario_design.md").read_text(encoding="utf-8")
    assert "Studio: studio.md" in nav
    assert "dashboard.md" not in nav
    assert "streamlit run" not in scenario_docs
