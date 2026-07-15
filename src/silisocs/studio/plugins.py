"""Studio page entry-point discovery."""

from __future__ import annotations

import importlib.metadata
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
        candidate = entry_point.load()
        page = (
            candidate()
            if callable(candidate) and not isinstance(candidate, StudioPage)
            else candidate
        )
        if not isinstance(page, StudioPage):
            raise TypeError(f"Studio page entry point {entry_point.name!r} must return StudioPage")
        pages.append(page)
    return sorted(pages, key=lambda page: page.label)
