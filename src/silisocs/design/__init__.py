"""Design system for silisocs UI surfaces — tokens, CSS, and Plotly templating.

The single source of truth for colors, typography, and structural scales shared
by Studio, exported reports, and backend-declared platform viewers.
"""

from __future__ import annotations

from .css import css_variables
from .plotly import apply_template, register_template, template_layout
from .tokens import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_LINK,
    ACTION_COLOR_FALLBACK,
    ACTION_COLORS,
    BORDER,
    CANVAS,
    CATEGORICAL_COLORS,
    DANGER,
    FONT_DISPLAY,
    FONT_STACK,
    INK,
    INK_MUTED,
    MOTION,
    RADII,
    SPACING,
    SUCCESS,
    SURFACE,
    SURFACE_SUBTLE,
    THEMES,
    TYPE_SCALE,
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
    "FONT_DISPLAY",
    "FONT_STACK",
    "INK",
    "INK_MUTED",
    "MOTION",
    "RADII",
    "SPACING",
    "SUCCESS",
    "SURFACE",
    "SURFACE_SUBTLE",
    "THEMES",
    "TYPE_SCALE",
    "WARNING",
    "action_color",
    "apply_template",
    "css_variables",
    "register_template",
    "template_layout",
]
