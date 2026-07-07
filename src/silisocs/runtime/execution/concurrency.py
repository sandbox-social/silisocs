"""Small concurrency helpers used by native runtime components."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from collections.abc import Callable, Coroutine, Mapping
from concurrent.futures import ThreadPoolExecutor
from typing import Any

# Default worker cap: one task per thread up to this many. Sizing the pool to
# len(tasks) fails outright at scale — a task per agent means one OS thread per
# agent (~8 MB stack each), which hits "can't start new thread" around a
# thousand agents.
_DEFAULT_MAX_WORKERS = 32


def run_tasks(
    tasks: Mapping[str, Callable[[], Any]], *, max_workers: int | None = None
) -> dict[str, Any]:
    """Run named callables on a bounded pool and return their results by name."""
    if not tasks:
        return {}
    workers = max(1, min(len(tasks), max_workers or _DEFAULT_MAX_WORKERS))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {name: executor.submit(fn) for name, fn in tasks.items()}
        return {name: future.result() for name, future in futures.items()}


class EventLoopThread:
    """One long-lived asyncio event loop on a background thread.

    The asyncio turn executor runs every in-flight turn as a coroutine on this
    single loop; the (synchronous) scheduling machinery submits coroutines from
    its own threads via :meth:`submit` and blocks on the returned futures —
    which is what preserves the existing barrier/ordering semantics. Coroutines
    that need blocking work (sync agents, observe/resolve sections, thread-
    wrapped model calls) hop out via ``asyncio.to_thread``, which uses the
    loop's default executor (bounded at ``min(32, cpu + 4)`` workers).
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="silisocs-async-turns", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    @property
    def alive(self) -> bool:
        """Whether the loop thread is still accepting work."""
        return self._thread.is_alive() and not self._loop.is_closed()

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        """Schedule a coroutine on the loop; the future resolves with its result."""
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self) -> None:
        """Stop the loop and join its thread. Callers drain their futures first."""
        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join()
        if not self._loop.is_closed():
            self._loop.close()
