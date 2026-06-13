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


class build_py(_build_py):  # noqa: N801 (setuptools naming convention)
    def run(self) -> None:
        super().run()
        source = _REPO_ROOT / "scenarios"
        if not source.is_dir():
            return
        destination = Path(self.build_lib) / "silisocs" / "scenarios"
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=_IGNORE)


setup(cmdclass={"build_py": build_py})
