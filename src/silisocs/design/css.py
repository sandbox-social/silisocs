"""CSS custom-property emission from the design tokens.

Studio (and any HTML surface) serves a light ``:root{...}`` block plus a
``[data-theme="dark"]{...}`` override built from :data:`THEMES`, so the same role
names drive both themes. The CSS variable names are the historical Studio
spellings (``--accent-dark`` for ``accent_hover``, ``--muted`` for ``ink_muted``,
``--surface-2`` for ``surface_subtle``); everything else is 1:1.
"""

from __future__ import annotations

from .tokens import FONT_DISPLAY, FONT_STACK, THEMES

# Role name (snake_case in THEMES) -> CSS custom-property name.
_VAR_NAMES: dict[str, str] = {
    "accent": "--accent",
    "accent_hover": "--accent-dark",
    "accent_link": "--accent-link",
    "ink": "--ink",
    "ink_muted": "--muted",
    "canvas": "--canvas",
    "surface": "--surface",
    "surface_subtle": "--surface-2",
    "border": "--border",
    "success": "--success",
    "warning": "--warning",
    "danger": "--danger",
}


def _block(selector: str, theme: dict[str, str], *, with_fonts: bool) -> str:
    decls = [f"{var}:{theme[role]}" for role, var in _VAR_NAMES.items() if role in theme]
    if with_fonts:
        decls.append(f"--font:{FONT_STACK}")
        decls.append(f"--font-display:{FONT_DISPLAY}")
    return selector + "{" + ";".join(decls) + "}"


def css_variables() -> str:
    """Return a light ``:root`` block followed by a ``[data-theme="dark"]`` block."""
    light = _block(":root", THEMES["light"], with_fonts=True)
    dark = _block('[data-theme="dark"]', THEMES["dark"], with_fonts=False)
    return light + "\n" + dark
