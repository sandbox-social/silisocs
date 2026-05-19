"""Small concurrency helpers used by native runtime components."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_tasks(tasks: Mapping[str, Callable[[], Any]]) -> dict[str, Any]:
    """Run named callables concurrently and return their results by name."""
    if not tasks:
        return {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {name: executor.submit(fn) for name, fn in tasks.items()}
        return {name: future.result() for name, future in futures.items()}
