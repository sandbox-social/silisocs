"""Tests for the silisocs.design system: token contrast, CSS emission, Plotly
templating, and custom-action color stability.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from silisocs.design import css, plotly, tokens


# ---------------------------------------------------------------------------
# WCAG relative-luminance contrast (stdlib only).
# ---------------------------------------------------------------------------
def _linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def _contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme_name", ["light", "dark"])
def test_theme_text_contrast(theme_name: str) -> None:
    theme = tokens.THEMES[theme_name]
    # Body text must be readable on both surface and canvas.
    assert _contrast(theme["ink"], theme["surface"]) >= 4.5
    assert _contrast(theme["ink"], theme["canvas"]) >= 4.5
    # Muted secondary text on surface.
    assert _contrast(theme["ink_muted"], theme["surface"]) >= 4.5
    # Small bold eyebrows / links use accent_hover — the relaxed 3.0 bar.
    assert _contrast(theme["accent_hover"], theme["surface"]) >= 3.0
    assert _contrast(theme["accent_link"], theme["surface"]) >= 4.5
    # (border on surface is intentionally exempt — it is decorative, not text.)


def test_both_themes_present_with_same_roles() -> None:
    assert set(tokens.THEMES) == {"light", "dark"}
    assert set(tokens.THEMES["light"]) == set(tokens.THEMES["dark"])


def test_light_constants_mirror_light_theme() -> None:
    light = tokens.THEMES["light"]
    assert light["accent"] == tokens.ACCENT
    assert light["accent_hover"] == tokens.ACCENT_HOVER
    assert light["ink"] == tokens.INK
    assert light["ink_muted"] == tokens.INK_MUTED
    assert light["surface"] == tokens.SURFACE
    assert light["canvas"] == tokens.CANVAS
    assert light["border"] == tokens.BORDER


# ---------------------------------------------------------------------------
# CSS emission.
# ---------------------------------------------------------------------------
def test_css_variables_light_and_dark_blocks() -> None:
    out = css.css_variables()
    assert ":root{" in out
    assert '[data-theme="dark"]{' in out
    assert "--accent:#0d9488" in out
    # Renamed roles carry their historical CSS spellings.
    assert "--accent-dark:" in out
    assert "--muted:" in out
    assert "--surface-2:" in out
    assert "--font:" in out
    assert "--font-display:" in out


def test_css_dark_block_overrides_canvas() -> None:
    out = css.css_variables()
    dark_block = out.split('[data-theme="dark"]{', 1)[1]
    assert "--canvas:#0f1117" in dark_block


# ---------------------------------------------------------------------------
# Plotly templating.
# ---------------------------------------------------------------------------
def test_apply_template_panel_keys_win() -> None:
    figure = {
        "data": [{"x": [1, 2], "y": [3, 4]}],
        "layout": {
            "xaxis": {"title": "Steps"},
            "font": {"color": "#123456"},
        },
    }
    out = plotly.apply_template(figure)
    layout = out["layout"]

    # Panel-set keys are preserved.
    assert layout["xaxis"]["title"] == "Steps"
    assert layout["font"]["color"] == "#123456"
    # Defaults fill the gaps one level deep.
    assert layout["xaxis"]["gridcolor"] == tokens.BORDER
    assert layout["font"]["size"] == 12
    assert layout["paper_bgcolor"] == "rgba(0,0,0,0)"
    assert layout["plot_bgcolor"] == "rgba(0,0,0,0)"
    assert layout["margin"] == {"l": 52, "r": 24, "t": 42, "b": 52}
    assert len(layout["colorway"]) == 8


def test_apply_template_does_not_mutate_input() -> None:
    figure: dict[str, Any] = {
        "data": [{"x": [1], "y": [2]}],
        "layout": {"xaxis": {"title": "Steps"}},
    }
    snapshot = copy.deepcopy(figure)
    out = plotly.apply_template(figure)

    assert figure == snapshot  # input untouched
    assert out is not figure
    assert out["layout"] is not figure["layout"]
    # No gridcolor leaked back into the caller's layout.
    assert "gridcolor" not in figure["layout"]["xaxis"]


def test_apply_template_data_untouched() -> None:
    data = [{"x": [1, 2, 3], "y": [4, 5, 6], "type": "scatter"}]
    figure = {"data": data}
    out = plotly.apply_template(figure)
    assert out["data"] == data
    assert out["data"] is data  # passed through by reference, unmodified


def test_apply_template_panel_gridcolor_wins() -> None:
    figure = {"layout": {"xaxis": {"gridcolor": "#ff0000"}}}
    out = plotly.apply_template(figure)
    assert out["layout"]["xaxis"]["gridcolor"] == "#ff0000"


def test_custom_action_colors_are_stable_without_registration() -> None:
    assert tokens.action_color("trade_asset") == tokens.action_color("trade_asset")
    assert tokens.action_color("trade_asset") in tokens.CATEGORICAL_COLORS
    assert tokens.action_color("reply") == tokens.ACTION_COLORS["reply"]
