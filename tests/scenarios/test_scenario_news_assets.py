"""Media referenced by the bundled news data must exist in the repository.

The election scenario's `news_account` class is configured with
`include_news_images: true`, so the persona pipeline reads each headline's first
list entry (an image path) straight out of these JSON files and hands it to the
agent as content. The paths were repo-root relative and went stale when
`examples/` was renamed to `scenarios/`, which nothing detected because no
loader opens them. This test is that detector: every path a shipped news file
references must resolve from the repository root.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
NEWS_DIRS = sorted((REPO_ROOT / "scenarios").glob("*/input/news_data"))


def _image_paths(news_file: Path) -> list[str]:
    """Return every media path a news JSON references (headline -> [path, ...])."""
    data = json.loads(news_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):  # headline-only files are plain lists
        return []
    return [item for value in data.values() if isinstance(value, list) for item in value]


def test_news_data_dirs_are_discovered() -> None:
    """Guard the glob itself: an empty sweep would make the test below vacuous."""
    assert NEWS_DIRS, "no scenario input/news_data directories found"


@pytest.mark.parametrize("news_dir", NEWS_DIRS, ids=lambda p: p.parts[-3])
def test_referenced_news_images_exist(news_dir: Path) -> None:
    """Every media path in a scenario's news JSONs resolves from the repo root."""
    missing: list[str] = []
    referenced = 0
    for news_file in sorted(news_dir.glob("*.json")):
        for path_str in _image_paths(news_file):
            referenced += 1
            if not (REPO_ROOT / path_str).is_file():
                missing.append(f"{news_file.name}: {path_str}")
    assert not missing, "news files reference missing media:\n" + "\n".join(missing)
    assert referenced, f"{news_dir} has no media references to check"
