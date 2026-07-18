"""Shared Matplotlib styling for static Silisocs analysis artifacts."""

from __future__ import annotations

import importlib
from contextlib import suppress
from pathlib import Path
from typing import Any

from silisocs.design.tokens import (
    BORDER,
    CATEGORICAL_COLORS,
    FONT_STACK,
    INK,
    INK_MUTED,
    SURFACE,
)


def apply_matplotlib_theme(matplotlib: Any) -> None:
    """Apply the light visual identity to an imported Matplotlib package."""
    font = FONT_STACK.split(",", 1)[0].strip("'\"")
    font_path = Path(__file__).parent / "fonts" / "manrope-latin-variable.woff2"
    with suppress(ImportError, AttributeError, OSError, RuntimeError):
        # Older Matplotlib/fontconfig builds may not support variable WOFF2.
        # The fallback stack below keeps static exports readable in that case.
        font_manager = importlib.import_module("matplotlib.font_manager")
        font_manager.fontManager.addfont(font_path)
    matplotlib.rcParams.update(
        {
            "axes.axisbelow": True,
            "axes.edgecolor": BORDER,
            "axes.labelcolor": INK,
            "axes.labelsize": 11,
            "axes.spines.right": False,
            "axes.spines.top": False,
            # Same series order as the Plotly template's colorway, so one
            # dataset gets identical series colors in both renderers.
            "axes.prop_cycle": matplotlib.cycler(color=list(CATEGORICAL_COLORS)),
            "axes.titlecolor": INK,
            "axes.titlesize": 15,
            "axes.titleweight": 700,
            "figure.facecolor": SURFACE,
            "font.family": "sans-serif",
            "font.sans-serif": [font, "DejaVu Sans"],
            "grid.color": BORDER,
            "grid.linewidth": 0.7,
            "legend.frameon": False,
            "savefig.facecolor": SURFACE,
            "savefig.bbox": "tight",
            "text.color": INK,
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
        }
    )
