"""Design tokens — the single source of truth for silisocs UI surfaces.

Every color, font, and structural scale used across Studio, exported reports,
backend visualizers, and future UI surfaces is defined here, so the same role
and action type render consistently everywhere.

``THEMES`` holds the full light and dark role palettes. The module-level
``ACCENT``/``INK``/... constants expose the light theme for direct consumers.
"""

from __future__ import annotations

import hashlib

# ---------------------------------------------------------------------------
# Themes: the canonical role palettes.
#
# Role names are snake_case. Light values are the historical Slate & Teal brand
# (also used by the docs site). Dark values are a coherent slate set anchored on
# canvas #0f1117, surface #161922, and ink #e8eaf2; the accent stays #0d9488 while
# text-on-dark accent/link roles brighten to teal-300/-200 where contrast
# requires. All pairs used for text are validated for WCAG contrast in
# tests/test_design.py.
# ---------------------------------------------------------------------------
THEMES: dict[str, dict[str, str]] = {
    "light": {
        "accent": "#0d9488",
        "accent_hover": "#0f766e",
        "accent_link": "#0f766e",
        "ink": "#17202a",
        "ink_muted": "#65717e",
        "canvas": "#eef1f2",
        "surface": "#ffffff",
        "surface_subtle": "#f4f6f7",
        "border": "#d9dfe2",
        "success": "#16865c",
        "warning": "#b7791f",
        "danger": "#c2414b",
    },
    "dark": {
        "accent": "#0d9488",
        "accent_hover": "#2dd4bf",
        "accent_link": "#5eead4",
        "ink": "#e8eaf2",
        "ink_muted": "#9aa7bd",
        "canvas": "#0f1117",
        "surface": "#161922",
        "surface_subtle": "#1e2230",
        "border": "#2b3140",
        "success": "#34d399",
        "warning": "#fbbf24",
        "danger": "#f87171",
    },
}

# ---------------------------------------------------------------------------
# Backward-compatible LIGHT-theme constants (exact historical names). Derived
# from THEMES["light"] so there is a single source of truth.
# ---------------------------------------------------------------------------
_LIGHT = THEMES["light"]

ACCENT = _LIGHT["accent"]
ACCENT_HOVER = _LIGHT["accent_hover"]
ACCENT_LINK = _LIGHT["accent_link"]
INK = _LIGHT["ink"]
INK_MUTED = _LIGHT["ink_muted"]
SURFACE = _LIGHT["surface"]
SURFACE_SUBTLE = _LIGHT["surface_subtle"]
CANVAS = _LIGHT["canvas"]
BORDER = _LIGHT["border"]
SUCCESS = _LIGHT["success"]
WARNING = _LIGHT["warning"]
DANGER = _LIGHT["danger"]

# ---------------------------------------------------------------------------
# Typography.
# ---------------------------------------------------------------------------
FONT_STACK = "'Inter', 'Segoe UI', system-ui, -apple-system, sans-serif"
FONT_DISPLAY = "Georgia, 'Times New Roman', serif"

# ---------------------------------------------------------------------------
# Optional house-color overrides for familiar action labels. ``action_color``
# deterministically assigns every other label, including custom-backend actions.
# ---------------------------------------------------------------------------
ACTION_COLORS: dict[str, str] = {
    "post": "#277da1",
    "like": "#43aa8b",
    "repost": "#f8961e",
    "reply": "#7b61a8",
    "comment": "#8f6b55",
    "upvote": "#168aad",
    "downvote": "#d1495b",
    "posted": "#1f77b4",
    "liked": "#2ca02c",
    "reposted": "#ff7f0e",
    "replied": "#9467bd",
    "commented": "#8c564b",
    "upvoted": "#17becf",
    "downvoted": "#d62728",
}

# Neutral fallback for clients that cannot call ``action_color`` directly.
ACTION_COLOR_FALLBACK = "#7f7f7f"

CATEGORICAL_COLORS: tuple[str, ...] = (
    "#277da1",
    "#43aa8b",
    "#f8961e",
    "#7b61a8",
    "#8f6b55",
    "#168aad",
    "#d1495b",
    "#0d9488",
)


def action_color(label: str) -> str:
    """Return an override or replay-stable categorical color for any action label."""
    key = str(label)
    if key in ACTION_COLORS:
        return ACTION_COLORS[key]
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return CATEGORICAL_COLORS[int.from_bytes(digest[:2], "big") % len(CATEGORICAL_COLORS)]


# ---------------------------------------------------------------------------
# Structural token scales (shared by studio.css and Plotly templates).
# ---------------------------------------------------------------------------
# Type sizes in px, matching studio.css usage (display h1 / object h1 / h2 /
# body / small / eyebrow).
TYPE_SCALE: dict[str, int] = {
    "display": 34,
    "h1": 31,
    "h2": 22,
    "body": 14,
    "small": 11,
    "eyebrow": 10,
}

# 4-based spacing scale (px strings).
SPACING: dict[str, str] = {
    "xs": "4px",
    "sm": "8px",
    "md": "12px",
    "lg": "16px",
    "xl": "24px",
    "2xl": "32px",
    "3xl": "48px",
}

RADII: dict[str, str] = {"base": "6px"}

MOTION: dict[str, str] = {"fast": "120ms", "base": "180ms"}
