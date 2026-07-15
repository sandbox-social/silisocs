"""Deprecated shim — use :mod:`silisocs.design.tokens` instead.

This module is retained for one release for backward compatibility. All names it
exported now live in ``silisocs.design.tokens`` (the single source of truth for
the brand tokens, now covering both light and dark themes). Import from
``silisocs.design`` going forward.
"""

from __future__ import annotations

from silisocs.design.tokens import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_LINK,
    ACTION_COLOR_FALLBACK,
    ACTION_COLORS,
    BORDER,
    CANVAS,
    CATEGORICAL_COLORS,
    DANGER,
    FONT_STACK,
    INK,
    INK_MUTED,
    SUCCESS,
    SURFACE,
    SURFACE_SUBTLE,
    WARNING,
    action_color,
)

__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_LINK",
    "ACTION_COLORS",
    "ACTION_COLOR_FALLBACK",
    "BORDER",
    "CANVAS",
    "CATEGORICAL_COLORS",
    "DANGER",
    "FONT_STACK",
    "INK",
    "INK_MUTED",
    "SUCCESS",
    "SURFACE",
    "SURFACE_SUBTLE",
    "WARNING",
    "action_color",
]
