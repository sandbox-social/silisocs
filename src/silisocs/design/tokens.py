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
# Role names are snake_case. The palette is intentionally broader than a normal
# dashboard palette: teal identifies interaction, while coral, violet, sky, and
# amber give simulations enough visual energy to express distinct states. Both
# themes use the same roles, and every foreground/background pair is validated
# for WCAG contrast in tests/test_design.py.
# ---------------------------------------------------------------------------
THEMES: dict[str, dict[str, str]] = {
    "light": {
        "accent": "#087f75",
        "accent_hover": "#07675f",
        "accent_link": "#076d65",
        "accent_soft": "#d9f2ee",
        "ink": "#15211f",
        "ink_muted": "#5f6f6b",
        "canvas": "#f2f5f2",
        "surface": "#ffffff",
        "surface_subtle": "#eaf0ed",
        "surface_elevated": "#ffffff",
        "border": "#d5e0db",
        "success": "#137a52",
        "warning": "#a9650f",
        "danger": "#bd3b4b",
        "on_strong": "#ffffff",
        "on_accent": "#ffffff",
        "signal_coral": "#ec6a5c",
        "signal_violet": "#8066c2",
        "signal_sky": "#3188c9",
        "signal_amber": "#db9518",
        "terminal_canvas": "#101817",
        "terminal_ink": "#dce9e5",
        "terminal_muted": "#8da39d",
        "terminal_success": "#a9e8c1",
        "terminal_warning": "#ffd58a",
        "terminal_danger": "#ff9d9d",
        "terminal_accent": "#75d4ca",
        "rail_canvas": "#ffffff",
        "rail_surface": "#dff2ee",
        "rail_hover": "#edf5f2",
        "rail_ink": "#15211f",
        "rail_muted": "#52635f",
        "rail_faint": "#70817d",
        "rail_border": "#d5e0db",
    },
    "dark": {
        "accent": "#4dd4c4",
        "accent_hover": "#72e2d5",
        "accent_link": "#72e2d5",
        "accent_soft": "#173b37",
        "ink": "#eef5f2",
        "ink_muted": "#a3b3ae",
        "canvas": "#101412",
        "surface": "#171d1b",
        "surface_subtle": "#212a27",
        "surface_elevated": "#1b2320",
        "border": "#303c38",
        "success": "#52d79b",
        "warning": "#f2ba55",
        "danger": "#ff8290",
        "on_strong": "#ffffff",
        "on_accent": "#082b27",
        "signal_coral": "#ff8174",
        "signal_violet": "#ae91ef",
        "signal_sky": "#65b9ef",
        "signal_amber": "#f5b84e",
        "terminal_canvas": "#0c1211",
        "terminal_ink": "#dce7e9",
        "terminal_muted": "#8fa2aa",
        "terminal_success": "#a9e8c1",
        "terminal_warning": "#ffd58a",
        "terminal_danger": "#ff9d9d",
        "terminal_accent": "#75d4ca",
        "rail_canvas": "#151b19",
        "rail_surface": "#203a35",
        "rail_hover": "#202c28",
        "rail_ink": "#e6f0ed",
        "rail_muted": "#a2b4af",
        "rail_faint": "#7f938e",
        "rail_border": "#303c38",
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
FONT_STACK = "'Manrope', 'Segoe UI Variable', 'Segoe UI', system-ui, -apple-system, sans-serif"
FONT_DISPLAY = FONT_STACK

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

# Conventional tag colors. These names get familiar ordering and colors, but
# tags are an open language and :func:`tag_color` handles every other name.
GROUP_COLORS: dict[str, str] = {
    "creates_content": CATEGORICAL_COLORS[0],
    "endorses": CATEGORICAL_COLORS[1],
    "negative": CATEGORICAL_COLORS[6],
    "social_graph": CATEGORICAL_COLORS[3],
    "reads": "#8a979d",
    "other": "#b9c2c6",
}

# Highlight ring for selected/focused graph nodes (theme-independent).
HIGHLIGHT = CATEGORICAL_COLORS[2]


def action_color(label: str) -> str:
    """Return a replay-stable categorical color for any action label."""
    key = str(label)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return CATEGORICAL_COLORS[int.from_bytes(digest[:2], "big") % len(CATEGORICAL_COLORS)]


def tag_color(tag: str) -> str:
    """Return a conventional or replay-stable categorical color for any tag."""
    key = str(tag)
    if key in GROUP_COLORS:
        return GROUP_COLORS[key]
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return CATEGORICAL_COLORS[int.from_bytes(digest[:2], "big") % len(CATEGORICAL_COLORS)]


# ---------------------------------------------------------------------------
# Structural token scales (shared by studio.css and Plotly templates).
# ---------------------------------------------------------------------------
# Type sizes in px, matching studio.css usage (display h1 / object h1 / h2 /
# body / small / eyebrow).
TYPE_SCALE: dict[str, int] = {
    "display": 42,
    "h1": 34,
    "h2": 23,
    "h3": 15,
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

RADII: dict[str, str] = {
    "xs": "9px",
    "sm": "13px",
    "base": "18px",
    "lg": "24px",
    "pill": "999px",
}

# Elevation is intentionally restrained: borders still define structure while
# these shadows separate only floating chrome and primary surfaces.
ELEVATION: dict[str, str] = {
    "sm": "0 1px 2px rgba(15, 32, 27, 0.06), 0 4px 14px rgba(15, 32, 27, 0.04)",
    "md": "0 14px 36px rgba(15, 32, 27, 0.10)",
    "lg": "0 28px 72px rgba(15, 32, 27, 0.16)",
}

MOTION: dict[str, str] = {"fast": "140ms", "base": "240ms", "slow": "520ms"}
