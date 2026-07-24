"""Design system for silisocs UI surfaces — tokens, CSS, and Plotly templating.

The single source of truth for colors, typography, and structural scales shared
by Studio, exported reports, and backend-declared platform viewers.
"""

from __future__ import annotations

from .css import css_variables
from .plotly import apply_template, template_layout
from .tokens import (
    ACCENT,
    ACCENT_HOVER,
    ACCENT_LINK,
    BORDER,
    CANVAS,
    CATEGORICAL_COLORS,
    DANGER,
    ELEVATION,
    FONT_DISPLAY,
    FONT_STACK,
    GROUP_COLORS,
    HIGHLIGHT,
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
    tag_color,
)

__all__ = [
    "ACCENT",
    "ACCENT_HOVER",
    "ACCENT_LINK",
    "BORDER",
    "CANVAS",
    "CATEGORICAL_COLORS",
    "DANGER",
    "ELEVATION",
    "FONT_DISPLAY",
    "FONT_STACK",
    "GROUP_COLORS",
    "HIGHLIGHT",
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
    "tag_color",
    "template_layout",
]
