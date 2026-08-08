"""The runtime kernel stays importable without dragging in the upper layers.

`import-linter` (see `[tool.importlinter]` in pyproject) proves the *static* graph;
this proves the runtime one for `silisocs.runtime.concurrency`, which moved out of
`silisocs.runtime.execution` precisely so `simulation_engines` and `initialization`
stop importing the orchestration package to get it.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

# Package `__init__` modules that get imported just to reach a kernel module.
_PATH_PACKAGES = {"silisocs", "silisocs.runtime"}


def _silisocs_modules_after_importing(module: str) -> set[str]:
    """Return the `silisocs.*` modules present in a fresh interpreter after an import."""
    code = (
        "import importlib, json, sys\n"
        f"importlib.import_module({module!r})\n"
        "print(json.dumps(sorted(m for m in sys.modules if m.split('.')[0] == 'silisocs')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    return set(json.loads(out))


def test_concurrency_pulls_in_no_other_silisocs_module() -> None:
    loaded = _silisocs_modules_after_importing("silisocs.runtime.concurrency")
    assert loaded - _PATH_PACKAGES == {"silisocs.runtime.concurrency"}


def test_concurrency_exports_survived_the_move() -> None:
    from silisocs.runtime.concurrency import EventLoopThread, run_tasks

    assert run_tasks({"a": lambda: 1, "b": lambda: 2}) == {"a": 1, "b": 2}
    assert callable(EventLoopThread)


def test_old_execution_path_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("silisocs.runtime.execution.concurrency")
