"""Locate bundled study templates (repo checkout or packaged install)."""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def _repo_template_root() -> Path | None:
    for parent in (_PACKAGE_DIR, *_PACKAGE_DIR.parents):
        if (parent / "pyproject.toml").is_file():
            candidate = parent / "experiments" / "studies"
            return candidate if candidate.is_dir() else None
    return None


def _packaged_template_root() -> Path | None:
    candidate = _PACKAGE_DIR / "templates"
    return candidate if candidate.is_dir() else None


def study_template_path(name: str = "study_template_v1") -> Path:
    """Return the directory of a bundled study template.

    Prefers a repository checkout over the packaged copy so developers edit
    the live template.

    Raises
    ------
        FileNotFoundError: When the named template cannot be located.
    """
    repo_root = _repo_template_root()
    if repo_root is not None and (repo_root / name).is_dir():
        return repo_root / name
    packaged_root = _packaged_template_root()
    if packaged_root is not None and (packaged_root / name).is_dir():
        return packaged_root / name
    raise FileNotFoundError(f"Study template not found: {name!r}")
