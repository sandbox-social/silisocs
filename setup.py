"""Build hooks: bundle the scenario library into the wheel.

The top-level ``scenarios/`` directory is the development source of truth.
At build time it is copied into the package as ``silisocs/scenarios/`` so
pip-installed users get the bundled scenario library (resolved at runtime by
``silisocs.scenario_library``). All other metadata lives in pyproject.toml.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import setup
from setuptools.command.build_py import build_py as _build_py

_REPO_ROOT = Path(__file__).resolve().parent
_IGNORE = shutil.ignore_patterns(
    "outputs",
    "__pycache__",
    "*.pyc",
    ".DS_Store",
    "*.db",
    "*.db-shm",
    "*.db-wal",
)


def _bundle_tree(build_lib: str, source: Path, package_subdir: str) -> None:
    if not source.is_dir():
        return
    destination = Path(build_lib) / "silisocs" / package_subdir
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=_IGNORE)


class build_py(_build_py):  # noqa: N801 (setuptools naming convention)
    def run(self) -> None:
        super().run()
        # Bundle the scenario library and the study template so pip-installed
        # users get both without a repo checkout.
        _bundle_tree(self.build_lib, _REPO_ROOT / "scenarios", "scenarios")
        _bundle_tree(
            self.build_lib,
            _REPO_ROOT / "experiments" / "studies" / "study_template_v1",
            "studies/templates/study_template_v1",
        )


setup(cmdclass={"build_py": build_py})
