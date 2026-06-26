"""Locate bundled study templates within a repository checkout.

Study templates live under ``experiments/studies/`` in the repository and are
not packaged into the wheel. Use this from a repo checkout to locate the
canonical study scaffold.
"""

from __future__ import annotations

from pathlib import Path

_PACKAGE_DIR = Path(__file__).resolve().parent


def _repo_template_root() -> Path | None:
    for parent in (_PACKAGE_DIR, *_PACKAGE_DIR.parents):
        if (parent / "pyproject.toml").is_file():
            candidate = parent / "experiments" / "studies"
            return candidate if candidate.is_dir() else None
    return None


def study_template_path(name: str = "study_template_v1") -> Path:
    """Return the directory of a study template within a repo checkout.

    Raises
    ------
        FileNotFoundError: When the named template cannot be located (e.g. in a
            pip-installed environment without a repo checkout).
    """
    repo_root = _repo_template_root()
    if repo_root is not None and (repo_root / name).is_dir():
        return repo_root / name
    raise FileNotFoundError(
        f"Study template {name!r} not found. Study templates live under "
        "experiments/studies/ in the repository and are not bundled with the "
        "installed package."
    )
