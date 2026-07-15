"""Shared Matplotlib styling for static Silisocs analysis artifacts."""

from __future__ import annotations

from typing import Any

from silisocs.design.tokens import ACCENT, BORDER, FONT_STACK, INK, INK_MUTED, SURFACE


def apply_matplotlib_theme(matplotlib: Any) -> None:
    """Apply the light visual identity to an imported Matplotlib package."""
    font = FONT_STACK.split(",", 1)[0].strip("'\"")
    matplotlib.rcParams.update(
        {
            "axes.edgecolor": BORDER,
            "axes.labelcolor": INK,
            "axes.prop_cycle": matplotlib.cycler(color=[ACCENT, "#277da1", "#f8961e", "#7b61a8"]),
            "axes.titlecolor": INK,
            "figure.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.sans-serif": [font, "DejaVu Sans"],
            "grid.color": BORDER,
            "savefig.facecolor": SURFACE,
            "text.color": INK,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
        }
    )
