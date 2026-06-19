"""Error-path tests for isolated failures in concurrent engine batches.

``RuntimeEngine._run_tasks_with_limit`` runs each agent turn in a thread pool and
must isolate a failing turn: the rest still complete, the failure is counted in
``agent_turn_failures``, and the failing task is recorded — never crashing the step.
"""

from __future__ import annotations

from silisocs.runtime.telemetry import SimMetricsCollector
from silisocs.simulation_engines.base_engines import RuntimeEngine


def test_batch_isolates_a_failing_agent_and_counts_it() -> None:
    SimMetricsCollector.reset()
    tasks = {
        "alice": lambda: "done:alice",
        "bob": _raise,
        "carol": lambda: "done:carol",
    }
    failed: list[str] = []
    results = RuntimeEngine._run_tasks_with_limit(tasks, 4, failed_tasks=failed)

    assert results["alice"] == "done:alice"
    assert results["carol"] == "done:carol"
    assert results["bob"] == ""  # isolated failure → empty result, not a crash
    assert failed == ["bob"]
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 1


def test_empty_batch_returns_empty_without_touching_counters() -> None:
    SimMetricsCollector.reset()
    assert RuntimeEngine._run_tasks_with_limit({}, 4, failed_tasks=[]) == {}
    assert SimMetricsCollector.get().counter("agent_turn_failures") == 0


def _raise() -> str:
    raise RuntimeError("agent turn blew up")
