"""Studio page entry-point discovery."""

from __future__ import annotations

import importlib.metadata
import warnings
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StudioPage:
    name: str
    label: str
    href: str
    router: Any


def load_studio_pages() -> list[StudioPage]:
    pages = []
    for entry_point in importlib.metadata.entry_points(group="silisocs.studio_pages"):
        # One broken third-party page must not abort Studio startup — skip it
        # with a warning instead (same policy as panel entry points).
        try:
            candidate = entry_point.load()
            page = (
                candidate()
                if callable(candidate) and not isinstance(candidate, StudioPage)
                else candidate
            )
            if not isinstance(page, StudioPage):
                raise TypeError(
                    f"Studio page entry point {entry_point.name!r} must return StudioPage"
                )
            pages.append(page)
        except Exception as exc:
            warnings.warn(
                f"Skipping Studio page entry point {entry_point.name!r}: {exc}",
                RuntimeWarning,
                stacklevel=2,
            )
    return sorted(pages, key=lambda page: page.label)
