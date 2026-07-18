"""Plotly figure templating from the design tokens.

:func:`apply_template` layers Studio's chart defaults (transparent background,
brand colorway, muted grid, Manrope font) *under* a panel's own layout so a panel
that sets e.g. ``xaxis.title`` keeps it while still gaining a default
``gridcolor``. The panel always wins on any key it sets; defaults only fill gaps.
The input figure is never mutated.
"""

from __future__ import annotations

import copy
from typing import Any

from .tokens import BORDER, CATEGORICAL_COLORS, INK, INK_MUTED, SURFACE

# Sans font used for chart chrome (independent of the CSS FONT_STACK quoting).
_FONT_FAMILY = "Manrope, Segoe UI Variable, Segoe UI, sans-serif"


def template_layout() -> dict[str, Any]:
    """Return the canonical Plotly template layout as plain JSON data."""
    return {
        "font": {"family": _FONT_FAMILY, "size": 12, "color": INK_MUTED},
        "title": {"font": {"family": _FONT_FAMILY, "size": 16, "color": INK}},
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"l": 52, "r": 24, "t": 42, "b": 52},
        "colorway": list(CATEGORICAL_COLORS),
        "xaxis": {
            "automargin": True,
            "gridcolor": BORDER,
            "linecolor": BORDER,
            "tickfont": {"color": INK_MUTED},
            "title": {"font": {"color": INK}},
            "zerolinecolor": BORDER,
        },
        "yaxis": {
            "automargin": True,
            "gridcolor": BORDER,
            "linecolor": BORDER,
            "tickfont": {"color": INK_MUTED},
            "title": {"font": {"color": INK}},
            "zerolinecolor": BORDER,
        },
        "legend": {
            "bgcolor": "rgba(0,0,0,0)",
            "font": {"color": INK_MUTED},
            "orientation": "h",
            "y": 1.06,
            "yanchor": "bottom",
        },
        "hoverlabel": {
            "bgcolor": SURFACE,
            "font": {"family": _FONT_FAMILY},
            "bordercolor": BORDER,
        },
        "hovermode": "closest",
    }


def register_template() -> None:
    """Register the ``silisocs`` template when the optional Plotly package exists."""
    try:
        import plotly.io as pio
    except ImportError:
        return
    pio.templates["silisocs"] = {"layout": template_layout()}


def _merge_under(default: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``override`` onto a copy of ``default``; override values win."""
    out = copy.deepcopy(default)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_under(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def apply_template(figure: dict) -> dict:
    """Return a new figure dict with Studio layout defaults under the panel's layout."""
    figure = figure or {}
    panel_layout = figure.get("layout") or {}
    merged = copy.deepcopy(panel_layout)

    defaults = template_layout()

    for key, default_value in defaults.items():
        if key not in merged:
            merged[key] = copy.deepcopy(default_value)
        elif isinstance(default_value, dict) and isinstance(merged[key], dict):
            # Panel keys win; defaults fill only the sub-keys the panel omitted.
            merged[key] = _merge_under(default_value, merged[key])
        # else: panel set a scalar for this key — it wins, leave it.

    new_figure = dict(figure)
    new_figure["layout"] = merged
    return new_figure
