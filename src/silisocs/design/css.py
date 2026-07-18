"""CSS custom-property emission from the design tokens.

Studio (and any HTML surface) serves a light ``:root{...}`` block plus a
``[data-theme="dark"]{...}`` override built from :data:`THEMES`, so the same role
names drive both themes. The CSS variable names are the historical Studio
spellings (``--accent-dark`` for ``accent_hover``, ``--muted`` for ``ink_muted``,
``--surface-2`` for ``surface_subtle``); everything else is 1:1.
"""

from __future__ import annotations

from pathlib import Path

from silisocs.design.tokens import (
    CATEGORICAL_COLORS,
    ELEVATION,
    FONT_DISPLAY,
    FONT_STACK,
    MOTION,
    RADII,
    SPACING,
    THEMES,
    TYPE_SCALE,
)

# Role name (snake_case in THEMES) -> CSS custom-property name.
_VAR_NAMES: dict[str, str] = {
    "accent": "--accent",
    "accent_hover": "--accent-dark",
    "accent_link": "--accent-link",
    "accent_soft": "--accent-soft",
    "ink": "--ink",
    "ink_muted": "--muted",
    "canvas": "--canvas",
    "surface": "--surface",
    "surface_subtle": "--surface-2",
    "surface_elevated": "--surface-elevated",
    "border": "--border",
    "success": "--success",
    "warning": "--warning",
    "danger": "--danger",
    "on_strong": "--on-strong",
    "on_accent": "--on-accent",
    "signal_coral": "--signal-coral",
    "signal_violet": "--signal-violet",
    "signal_sky": "--signal-sky",
    "signal_amber": "--signal-amber",
    "terminal_canvas": "--terminal-canvas",
    "terminal_ink": "--terminal-ink",
    "terminal_muted": "--terminal-muted",
    "terminal_success": "--terminal-success",
    "terminal_warning": "--terminal-warning",
    "terminal_danger": "--terminal-danger",
    "terminal_accent": "--terminal-accent",
    "rail_canvas": "--rail-canvas",
    "rail_surface": "--rail-surface",
    "rail_hover": "--rail-hover",
    "rail_ink": "--rail-ink",
    "rail_muted": "--rail-muted",
    "rail_faint": "--rail-faint",
    "rail_border": "--rail-border",
}


def _block(selector: str, theme: dict[str, str], *, with_structure: bool) -> str:
    decls = [f"{var}:{theme[role]}" for role, var in _VAR_NAMES.items() if role in theme]
    if with_structure:
        decls.append(f"--font:{FONT_STACK}")
        decls.append(f"--font-display:{FONT_DISPLAY}")
        decls.extend(f"--type-{name}:{value}px" for name, value in TYPE_SCALE.items())
        decls.extend(f"--radius-{name}:{value}" for name, value in RADII.items())
        decls.append(f"--radius:{RADII['base']}")
        decls.extend(f"--shadow-{name}:{value}" for name, value in ELEVATION.items())
        decls.extend(f"--space-{name}:{value}" for name, value in SPACING.items())
        decls.extend(f"--motion-{name}:{value}" for name, value in MOTION.items())
        decls.extend(
            f"--categorical-{index}:{color}" for index, color in enumerate(CATEGORICAL_COLORS)
        )
    return selector + "{" + ";".join(decls) + "}"


def css_variables(*, font_url: str | None = "manrope.woff2") -> str:
    """Return the canonical font, light-theme, and dark-theme CSS blocks.

    ``font_url`` is configurable because web surfaces serve the bundled font as
    a sibling asset while standalone reports embed it as a data URL. Pass
    ``None`` when the consumer provides its own font declaration.
    """
    font_face = ""
    if font_url is not None:
        font_face = (
            '@font-face{font-family:"Manrope";font-style:normal;font-weight:400 800;'
            f'font-display:swap;src:url("{font_url}") format("woff2")}}\n'
        )
    light = _block(":root", THEMES["light"], with_structure=True)
    dark = _block('[data-theme="dark"]', THEMES["dark"], with_structure=False)
    return font_face + light + "\n" + dark


def viewer_stylesheet() -> str:
    """Return the shared zero-build stylesheet for backend platform viewers."""
    shared = Path(__file__).parent / "components" / "viewer.css"
    return css_variables() + "\n" + shared.read_text(encoding="utf-8")


def viewer_script() -> str:
    """Return the shared zero-build JS runtime for backend platform viewers.

    Served at ``/assets/viewer.js`` (sibling to :func:`viewer_stylesheet`) so the
    microblog and forum templates load one copy of the theme bootstrap and the
    ``esc``/``escAttr``/``getColor``/``timeSince``/``wireActions`` helpers instead
    of each embedding its own.
    """
    shared = Path(__file__).parent / "components" / "viewer.js"
    return shared.read_text(encoding="utf-8")
