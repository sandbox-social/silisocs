"""Error-path tests for isolated failures in concurrent engine batches.

``RuntimeEngine._drain_tasks_on_pool`` is the single production drain primitive
under every traversal path: it runs each agent turn on a shared thread pool and
must isolate a failing turn: the rest still complete, the failure is counted in
``agent_turn_failures``, and the failing task is recorded — never crashing the step.
"""

from __future__ import annotations

import concurrent.futures
from types import SimpleNamespace

from silisocs.runtime.telemetry import SimMetricsCollector
from silisocs.simulation_engines.base_engines import RuntimeEngine


def _drain_via_pool(tasks, worker_limit, failed_tasks) -> None:
    """Thin test shim: exercise the production ``_drain_tasks_on_pool`` primitive on
    a real worker-limited pool (threads executor; the drain drives turns by side
    effect and records failures in ``failed_tasks``).
    """
    engine = SimpleNamespace(_async_turns=False)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, worker_limit)) as pool:
        RuntimeEngine._drain_tasks_on_pool(engine, pool, tasks, failed_tasks)  # type: ignore[arg-type]


def test_batch_isolates_a_failing_agent_and_counts_it() -> None:
    SimMetricsCollector.reset()
    ran: list[str] = []
    tasks = {
        "alice": lambda: ran.append("alice"),
        "bob": _raise,
        "carol": lambda: ran.append("carol"),
    }
    failed: list[str] = []
    _drain_via_pool(tasks, 4, failed)

    # The healthy turns still ran; the failing one is isolated (no crash), counted,
    # and recorded in failed_tasks.
    assert set(ran) == {"alice", "carol"}
    assert failed == ["bob"]
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 1


def test_empty_batch_drains_without_touching_counters() -> None:
    SimMetricsCollector.reset()
    failed: list[str] = []
    _drain_via_pool({}, 4, failed)
    assert failed == []
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 0


def _raise() -> str:
    raise RuntimeError("agent turn blew up")
